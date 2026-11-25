"""Deterministic in-memory filesystem source using the live adapter contract."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from sylphy_entity.contracts.observation import Observation
from .base import ObservationSink


class SimulatedFilesystemAdapter:
    def __init__(
        self, sink: ObservationSink, *, source_id: str = "simulated-filesystem",
        permission_scope: str = "simulated:sandbox",
    ) -> None:
        if not permission_scope.strip():
            raise ValueError("permission_scope must be non-blank")
        self._sink = sink
        self._source_id = source_id
        self._permission_scope = permission_scope
        self._files: dict[str, dict[str, Any]] = {}
        self._sequence = 0
        self._running = False

    @property
    def adapter_id(self) -> str:
        return self._source_id

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def discover(self, relative_path: str, *, size: int = 0, modified_ns: int = 1) -> Observation:
        path = self._normalize(relative_path)
        value = self._metadata(path, size=size, modified_ns=modified_ns)
        self._files[path] = value
        return await self._emit(path, "filesystem.file_discovered", value)

    async def create(self, relative_path: str, *, size: int = 0, modified_ns: int = 1) -> Observation:
        path = self._normalize(relative_path)
        kind = "filesystem.file_modified" if path in self._files else "filesystem.file_created"
        value = self._metadata(path, size=size, modified_ns=modified_ns)
        self._files[path] = value
        return await self._emit(path, kind, value)

    async def modify(self, relative_path: str, *, size: int, modified_ns: int) -> Observation:
        path = self._normalize(relative_path)
        if path not in self._files:
            return await self.create(path, size=size, modified_ns=modified_ns)
        value = self._metadata(path, size=size, modified_ns=modified_ns)
        self._files[path] = value
        return await self._emit(path, "filesystem.file_modified", value)

    async def delete(self, relative_path: str) -> Observation:
        path = self._normalize(relative_path)
        self._files.pop(path, None)
        return await self._emit(path, "filesystem.file_deleted", {})

    async def rename(self, old_relative_path: str, new_relative_path: str) -> Observation:
        old_path = self._normalize(old_relative_path)
        new_path = self._normalize(new_relative_path)
        old = self._files.pop(old_path, None)
        if old is None:
            raise FileNotFoundError(old_path)
        value = self._metadata(
            new_path,
            size=int(old["size_bytes"]),
            modified_ns=int(old["modified_ns"]),
        )
        self._files[new_path] = value
        return await self._emit(
            new_path,
            "filesystem.file_renamed",
            {**value, "old_relative_path": old_path},
        )

    async def unavailable(self, relative_path: str) -> Observation:
        path = self._normalize(relative_path)
        value = self._files.get(path, {})
        return await self._emit(path, "filesystem.file_unavailable", value, freshness="stale")

    def metadata_for(self, relative_path: str) -> dict[str, Any] | None:
        """Return a defensive metadata copy for a simulated capability worker."""
        path = self._normalize(relative_path)
        value = self._files.get(path)
        return dict(value) if value is not None else None

    def status(self) -> dict[str, object]:
        return {
            "id": self.adapter_id,
            "kind": "simulated_filesystem",
            "running": self._running,
            "files": len(self._files),
            "sequence": self._sequence,
        }

    async def _emit(
        self,
        relative_path: str,
        kind: str,
        payload: dict[str, Any],
        *,
        freshness: str = "current",
    ) -> Observation:
        if not self._running:
            raise RuntimeError("simulated filesystem adapter is not running")
        self._sequence += 1
        observation = Observation(
            source_id=self._source_id,
            subject=f"file:{relative_path}",
            kind=kind,
            payload=payload,
            observed_at=datetime.now(timezone.utc),
            sequence=self._sequence,
            verification="confirmed",
            permission_scope=self._permission_scope,
            freshness=freshness,
        )
        await self._sink(observation)
        return observation

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        windows_path = PureWindowsPath(value)
        if (
            path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("path must be a safe relative sandbox path")
        return path.as_posix()

    @staticmethod
    def _metadata(path: str, *, size: int, modified_ns: int) -> dict[str, Any]:
        if size < 0 or modified_ns < 0:
            raise ValueError("size and modified_ns must be non-negative")
        return {
            "relative_path": path,
            "size_bytes": size,
            "modified_ns": modified_ns,
            "is_directory": False,
        }
