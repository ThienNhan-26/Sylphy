"""Explicit one-frame webcam capture with process-bounded Windows device access."""

from __future__ import annotations

from dataclasses import dataclass
import multiprocessing
from multiprocessing.connection import Connection
from typing import Protocol

from PIL import Image

from .vision import VisionError


@dataclass(frozen=True)
class VisionConfig:
    enabled: bool = False
    model: str = ""
    camera_index: int = 0
    capture_timeout_seconds: float = 5
    max_width: int = 1280
    max_height: int = 1280
    jpeg_quality: int = 85
    max_image_bytes: int = 1_500_000
    inference_timeout_seconds: float = 90


class CameraError(VisionError):
    pass


class CameraBackend(Protocol):
    def capture(self, camera_index: int, config: VisionConfig) -> bytes: ...
    def probe(self, camera_index: int, timeout_seconds: float) -> bool: ...


def _fit_dimensions(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    if width < 1 or height < 1:
        raise CameraError("Captured image has invalid dimensions.")
    scale = min(1.0, max_width / width, max_height / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _encode_jpeg(image: Image.Image, config: VisionConfig) -> bytes:
    """Aspect-preserving in-memory JPEG encoding with a hard size ceiling."""
    image = image.convert("RGB")
    fitted = _fit_dimensions(*image.size, config.max_width, config.max_height)
    if fitted != image.size:
        image = image.resize(fitted, Image.Resampling.LANCZOS)
    for reduction in range(9):
        quality = max(40, config.jpeg_quality - reduction * 10)
        for _ in range(2):
            from io import BytesIO
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            encoded = buffer.getvalue()
            buffer.close()
            if len(encoded) <= config.max_image_bytes:
                return encoded
            if quality <= 40:
                break
            quality = max(40, quality - 10)
        if min(image.size) <= 64:
            break
        new_size = (max(1, round(image.width * 0.75)), max(1, round(image.height * 0.75)))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
    raise CameraError("Captured image exceeds the configured size limit after bounded compression.")


def _read_one_frame(cv2, camera_index: int):
    """Try DirectShow first, then CAP_ANY, releasing each handle before fallback."""
    backends = []
    directshow = getattr(cv2, "CAP_DSHOW", None)
    any_backend = getattr(cv2, "CAP_ANY", 0)
    if directshow is not None:
        backends.append(directshow)
    if any_backend not in backends:
        backends.append(any_backend)

    any_opened = False
    for backend in backends:
        capture = None
        frame = None
        release_failed = False
        try:
            capture = cv2.VideoCapture(camera_index, backend)
            if not capture.isOpened():
                pass
            else:
                any_opened = True
                ok, candidate = capture.read()
                if ok and candidate is not None and getattr(candidate, "size", 0) > 0:
                    frame = candidate
        except Exception:
            # A backend/driver failure is isolated; try the next API only after release.
            pass
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    release_failed = True
        if release_failed:
            return None, "release_failed"
        if frame is not None:
            return frame, None
    return None, "no_frame" if any_opened else "unavailable"


def _opencv_worker(connection: Connection, operation: str, camera_index: int,
                   config: VisionConfig | None) -> None:
    try:
        try:
            import cv2
        except ImportError as exc:
            connection.send(("error", "missing_dependency"))
            return
        if operation not in {"capture", "probe"} or (operation == "capture" and config is None):
            connection.send(("error", "invalid_operation"))
            return
        frame, failure = _read_one_frame(cv2, camera_index)
        if frame is None:
            connection.send(("error", failure))
            return
        if operation == "probe":
            connection.send(("ok", True))
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        encoded = _encode_jpeg(Image.fromarray(rgb), config)
        connection.send(("ok", encoded))
    except Exception as exc:
        try:
            name = "image_too_large" if isinstance(exc, CameraError) else type(exc).__name__
            connection.send(("error", name))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


class OpenCVCameraBackend:
    """Uses an isolated child so a blocked driver can be terminated at timeout."""

    def _run(self, operation: str, camera_index: int, config: VisionConfig | None,
             timeout_seconds: float):
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_opencv_worker,
                                  args=(sender, operation, camera_index, config), daemon=True)
        started = False
        try:
            process.start()
            started = True
            sender.close()
            if not receiver.poll(timeout_seconds):
                raise CameraError(f"Camera {operation} timed out after {timeout_seconds:g} seconds.")
            try:
                status, value = receiver.recv()
            except (EOFError, OSError) as exc:
                raise CameraError("Camera process ended without a result.") from exc
            if status != "ok":
                if value == "missing_dependency":
                    raise CameraError(
                        'Camera support is not installed. Run: python -m pip install -e ".[vision]"'
                    )
                if value == "unavailable":
                    raise CameraError(f"Camera {camera_index} is unavailable or busy.")
                if value == "no_frame":
                    raise CameraError("Camera did not return a usable frame.")
                if value == "release_failed":
                    raise CameraError("Camera handle could not be reliably released; fallback was not attempted.")
                if value == "image_too_large":
                    raise CameraError("Captured image exceeds the configured size limit after bounded compression.")
                raise CameraError(f"Camera operation failed ({value}).")
            return value
        finally:
            sender.close()
            receiver.close()
            if started:
                process.join(timeout=0.5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=1.0)
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join(timeout=1.0)

    def capture(self, camera_index: int, config: VisionConfig) -> bytes:
        return self._run("capture", camera_index, config, config.capture_timeout_seconds)

    def probe(self, camera_index: int, timeout_seconds: float) -> bool:
        try:
            return bool(self._run("probe", camera_index, None, timeout_seconds))
        except CameraError as exc:
            if "unavailable or busy" in str(exc) or "usable frame" in str(exc):
                return False
            raise


class CameraService:
    MAX_DEVICE_PROBES = 5

    def __init__(self, config: VisionConfig, backend: CameraBackend | None = None):
        self.config = config
        self._backend = backend or OpenCVCameraBackend()

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise CameraError("Vision is disabled. Set [vision].enabled = true in config.toml.")

    def capture_one(self) -> tuple[bytes, str]:
        self._require_enabled()
        try:
            image = self._backend.capture(self.config.camera_index, self.config)
        except CameraError:
            raise
        except Exception as exc:
            raise CameraError(f"Camera capture failed ({type(exc).__name__}).") from exc
        if not isinstance(image, bytes) or not image:
            raise CameraError("Camera returned no encoded image.")
        if len(image) > self.config.max_image_bytes:
            raise CameraError("Captured image exceeds the configured size limit.")
        return image, f"camera:{self.config.camera_index}"

    def devices(self) -> list[int]:
        self._require_enabled()
        available: list[int] = []
        for index in range(self.MAX_DEVICE_PROBES):
            try:
                if self._backend.probe(index, self.config.capture_timeout_seconds):
                    available.append(index)
            except CameraError as exc:
                if "timed out" in str(exc):
                    continue
                raise
            except Exception as exc:
                raise CameraError(f"Camera enumeration failed ({type(exc).__name__}).") from exc
        return available
