"""Consent-gated camera adapter with ephemeral frame processing.

Raw frames stay in process memory and are never placed in an Observation or
EntityEvent. A processor must convert each frame into bounded JSON facts.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from sylphy_entity.contracts.observation import Observation
from sylphy_entity.contracts.sensor import SensorKind
from .session import SensorAdapterSession, SensorSessionState


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    pixels: Any
    captured_at: datetime
    sequence: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        if self.sequence < 0:
            raise ValueError("frame sequence must be non-negative")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("frame dimensions must be positive")


class CameraBackend(Protocol):
    async def open(self) -> None: ...
    async def read(self) -> CapturedFrame | None: ...
    async def close(self) -> None: ...


class FrameProcessor(Protocol):
    async def process(self, frame: CapturedFrame) -> Mapping[str, Any]: ...


class CameraAdapter:
    """Runs a camera source only while consent and the device lease are active."""

    def __init__(
        self,
        *,
        device_id: str,
        session: SensorAdapterSession,
        backend: CameraBackend,
        processor: FrameProcessor,
        capture_interval_seconds: float = 1.0,
        max_frame_pixels: int = 1920 * 1080,
        max_payload_chars: int = 8192,
    ) -> None:
        if session.sensor is not SensorKind.CAMERA:
            raise ValueError("CameraAdapter requires a CAMERA sensor session")
        if session.device_id != device_id:
            raise ValueError("camera device_id must match sensor session")
        if capture_interval_seconds <= 0:
            raise ValueError("capture_interval_seconds must be positive")
        if max_frame_pixels <= 0 or max_payload_chars <= 0:
            raise ValueError("frame and payload limits must be positive")
        self.device_id = device_id
        self._session = session
        self._backend = backend
        self._processor = processor
        self._interval = capture_interval_seconds
        self._max_pixels = max_frame_pixels
        self._max_payload_chars = max_payload_chars
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._opened = False
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        await self._session.start()
        try:
            await self._backend.open()
            self._opened = True
            self._stop.clear()
            self.last_error = None
            self._task = asyncio.create_task(
                self._capture_loop(),
                name=f"camera-adapter:{self.device_id}",
            )
        except BaseException:
            await self._close_backend()
            await self._session.stop()
            raise

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._close_backend()
        await self._session.stop()

    async def _capture_loop(self) -> None:
        try:
            while not self._stop.is_set():
                frame = await self._backend.read()
                if frame is None:
                    await asyncio.sleep(self._interval)
                    continue
                if frame.width * frame.height > self._max_pixels:
                    raise ValueError("camera frame exceeds configured pixel limit")
                result = await self._processor.process(frame)
                if not isinstance(result, Mapping):
                    raise TypeError("frame processor must return a mapping")
                if _contains_binary(result):
                    raise ValueError("raw binary media is not accepted in camera observations")
                # Bound JSON metadata before it reaches the shared Observation path.
                import json
                encoded = json.dumps(dict(result), ensure_ascii=False, separators=(",", ":"))
                if len(encoded) > self._max_payload_chars:
                    raise ValueError("vision result exceeds configured payload limit")
                observation = Observation(
                    source_id=f"sensor:camera:{self.device_id}",
                    subject=f"camera:{self.device_id}",
                    kind="camera.frame.processed",
                    payload=dict(result),
                    observed_at=frame.captured_at,
                    received_at=datetime.now(timezone.utc),
                    sequence=frame.sequence,
                    verification="inferred",
                    permission_scope=self._session.active_permission_scope or "",
                    freshness="current",
                )
                await self._session.publish(observation)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            # Keep the adapter visibly RUNNING until cleanup has released the
            # device and its ResourceArbiter lease. Otherwise observers can see
            # running=False and race a still-pending session.stop().
            try:
                await self._close_backend()
            finally:
                try:
                    await self._session.stop()
                finally:
                    if asyncio.current_task() is self._task:
                        self._task = None

    async def _close_backend(self) -> None:
        if self._opened:
            self._opened = False
            await self._backend.close()


class MetadataFrameProcessor:
    """Safe baseline processor: emits only frame metadata and a one-way digest.

    This proves the camera capture boundary without claiming visual recognition.
    A later Vision inference slice can replace this processor through the same
    protocol; raw pixels still remain ephemeral.
    """

    async def process(self, frame: CapturedFrame) -> Mapping[str, Any]:
        pixels = frame.pixels
        to_bytes = getattr(pixels, "tobytes", None)
        if not callable(to_bytes):
            raise TypeError("frame pixels must expose tobytes()")
        digest = hashlib.sha256(to_bytes()).hexdigest()
        return {
            "width": frame.width,
            "height": frame.height,
            "sequence": frame.sequence,
            "frame_sha256": digest,
            "analysis": "metadata_only",
        }


class OpenCVCameraBackend:
    """Optional OpenCV backend; importing this module does not open hardware."""

    def __init__(self, device_index: int = 0, *, width: int = 640, height: int = 480) -> None:
        if device_index < 0 or width <= 0 or height <= 0:
            raise ValueError("invalid camera device index or dimensions")
        self.device_index = device_index
        self.width = width
        self.height = height
        self._capture: Any | None = None
        self._sequence = 0
        self._cv2: Any | None = None

    async def open(self) -> None:
        if self._capture is not None:
            return
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("install the optional vision extra to use OpenCV camera capture") from exc
        self._cv2 = cv2
        capture = await asyncio.to_thread(cv2.VideoCapture, self.device_index)
        if not capture.isOpened():
            await asyncio.to_thread(capture.release)
            raise RuntimeError(f"camera device {self.device_index} could not be opened")
        await asyncio.to_thread(capture.set, cv2.CAP_PROP_FRAME_WIDTH, self.width)
        await asyncio.to_thread(capture.set, cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._capture = capture

    async def read(self) -> CapturedFrame | None:
        if self._capture is None or self._cv2 is None:
            raise RuntimeError("camera backend is not open")
        ok, pixels = await asyncio.to_thread(self._capture.read)
        if not ok or pixels is None:
            return None
        height, width = pixels.shape[:2]
        frame = CapturedFrame(
            pixels=pixels,
            captured_at=datetime.now(timezone.utc),
            sequence=self._sequence,
            width=int(width),
            height=int(height),
        )
        self._sequence += 1
        return frame

    async def close(self) -> None:
        capture, self._capture = self._capture, None
        self._cv2 = None
        if capture is not None:
            await asyncio.to_thread(capture.release)



def _contains_binary(value: object) -> bool:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return True
    if isinstance(value, Mapping):
        return any(_contains_binary(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_contains_binary(item) for item in value)
    return False
