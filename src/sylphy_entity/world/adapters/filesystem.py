"""Read-only observer for one explicitly authorized sandbox directory.

Watchdog notifications are wake-up hints only. State is verified by periodic
reconciliation, so duplicate/missed OS notifications cannot corrupt the model.
"""

from __future__ import annotations

import asyncio
import os
import stat as stat_module
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sylphy_entity.contracts.observation import Observation
from .base import ObservationSink

try:  # Watchdog is an optional Stage-2 extra; polling remains a safe fallback.
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover - exercised on minimal environments
    FileSystemEventHandler = object  # type: ignore[assignment,misc]
    Observer = None  # type: ignore[assignment,misc]


@dataclass(frozen=True, slots=True)
class _FileEntry:
    relative_path: str
    size_bytes: int
    modified_ns: int
    device: int
    inode: int

    @property
    def fingerprint(self) -> tuple[int, int, int, int]:
        return (self.size_bytes, self.modified_ns, self.device, self.inode)

    @property
    def identity(self) -> tuple[int, int] | None:
        if self.inode <= 0:
            return None
        return self.device, self.inode

    def payload(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "modified_ns": self.modified_ns,
            "is_directory": False,
        }


class FilesystemAdapter:
    """Observe file metadata under one sandbox; never reads file contents."""

    def __init__(
        self,
        root: Path,
        sink: ObservationSink,
        *,
        reconcile_interval_seconds: float = 2.0,
        event_debounce_seconds: float = 0.12,
    ) -> None:
        if reconcile_interval_seconds < 0.1:
            raise ValueError("reconcile interval must be at least 0.1 seconds")
        if event_debounce_seconds < 0:
            raise ValueError("event debounce cannot be negative")
        self._configured_root = Path(root).expanduser()
        self._root: Path | None = None
        self._sink = sink
        self._interval = reconcile_interval_seconds
        self._debounce = event_debounce_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._observer: Any = None
        self._known: dict[str, _FileEntry] = {}
        self._stale_paths: set[str] = set()
        self._sequence = 0
        self._observations_emitted = 0
        self._last_scan_at: str | None = None
        self._last_error: str | None = None
        self._initialized = False
        self._rename_hints: list[tuple[str, str]] = []
        self._hint_lock = threading.Lock()

    @property
    def adapter_id(self) -> str:
        return "filesystem:sandbox"

    @property
    def root(self) -> Path:
        if self._root is None:
            raise RuntimeError("filesystem adapter has not started")
        return self._root

    @property
    def backend(self) -> str:
        return "watchdog+reconciliation" if Observer is not None else "polling-reconciliation"

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        root = self._configured_root.resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(f"sandbox root is not a directory: {root}")
        self._root = root
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()

        if Observer is not None:
            adapter = self

            class _Handler(FileSystemEventHandler):
                def on_any_event(self, event: Any) -> None:
                    if getattr(event, "is_directory", False):
                        adapter._signal_change()
                        return
                    if getattr(event, "event_type", "") == "moved":
                        adapter._remember_rename(
                            str(getattr(event, "src_path", "")),
                            str(getattr(event, "dest_path", "")),
                        )
                    adapter._signal_change()

            observer = Observer()
            observer.schedule(_Handler(), str(root), recursive=True)
            observer.start()
            self._observer = observer

        # Establish initial state before exposing the adapter as running.
        await self._scan_once()
        self._task = asyncio.create_task(self._run(), name="sylphy-filesystem-adapter")

    async def stop(self) -> None:
        observer = self._observer
        self._observer = None
        if observer is not None:
            observer.stop()
            await asyncio.to_thread(observer.join, 3.0)
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def status(self) -> dict[str, object]:
        return {
            "id": self.adapter_id,
            "kind": "filesystem",
            "running": self._task is not None and not self._task.done(),
            "root": str(self._root) if self._root else str(self._configured_root),
            "backend": self.backend,
            "reconcile_interval_seconds": self._interval,
            "known_files": len(self._known),
            "sequence": self._sequence,
            "observations_emitted": self._observations_emitted,
            "last_scan_at": self._last_scan_at,
            "last_error": self._last_error,
        }

    def _signal_change(self) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._wake.set)

    def _remember_rename(self, source: str, destination: str) -> None:
        if not source or not destination:
            return
        with self._hint_lock:
            self._rename_hints.append((source, destination))

    async def _run(self) -> None:
        while True:
            signalled = False
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
                signalled = True
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if signalled and self._debounce:
                await asyncio.sleep(self._debounce)
            try:
                await self._scan_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"

    async def _scan_once(self) -> None:
        current, inaccessible = await asyncio.to_thread(self._collect_snapshot)
        self._last_scan_at = datetime.now(timezone.utc).isoformat()
        self._last_error = None
        previous = self._known
        handled_old: set[str] = set()
        handled_new: set[str] = set()

        # Prefer explicit OS rename hints. The actual destination is still
        # checked against a fresh filesystem snapshot before publication.
        with self._hint_lock:
            hints = self._rename_hints
            self._rename_hints = []
        for source_abs, destination_abs in hints:
            old_rel = self._relative_from_absolute(source_abs)
            new_rel = self._relative_from_absolute(destination_abs)
            if old_rel and new_rel and old_rel in previous and new_rel in current:
                await self._emit_rename(old_rel, new_rel, current[new_rel])
                handled_old.add(old_rel)
                handled_new.add(new_rel)

        # Reconciliation can still identify a rename if the OS notification
        # was lost, using stable filesystem identity (device + inode/file ID).
        if self._initialized:
            removed = [key for key in previous if key not in current and key not in handled_old]
            added = [key for key in current if key not in previous and key not in handled_new]
            old_by_identity: dict[tuple[int, int], list[str]] = {}
            new_by_identity: dict[tuple[int, int], list[str]] = {}
            for key in removed:
                identity = previous[key].identity
                if identity is not None:
                    old_by_identity.setdefault(identity, []).append(key)
            for key in added:
                identity = current[key].identity
                if identity is not None:
                    new_by_identity.setdefault(identity, []).append(key)
            for identity, old_paths in old_by_identity.items():
                new_paths = new_by_identity.get(identity, [])
                if len(old_paths) == 1 and len(new_paths) == 1:
                    old_rel, new_rel = old_paths[0], new_paths[0]
                    old_entry, new_entry = previous[old_rel], current[new_rel]
                    same_content_metadata = (
                        old_entry.size_bytes == new_entry.size_bytes
                        and old_entry.modified_ns == new_entry.modified_ns
                    )
                    if same_content_metadata:
                        await self._emit_rename(old_rel, new_rel, new_entry)
                        handled_old.add(old_rel)
                        handled_new.add(new_rel)

        if not self._initialized:
            for relative, entry in sorted(current.items()):
                await self._emit("filesystem.file_discovered", relative, entry.payload())
            self._initialized = True
        else:
            for relative, entry in sorted(current.items()):
                if relative in handled_new:
                    self._stale_paths.discard(relative)
                    continue
                old = previous.get(relative)
                if relative in self._stale_paths:
                    await self._emit("filesystem.file_restored", relative, entry.payload())
                    self._stale_paths.discard(relative)
                elif old is None:
                    await self._emit("filesystem.file_created", relative, entry.payload())
                elif old.fingerprint != entry.fingerprint:
                    await self._emit("filesystem.file_modified", relative, entry.payload())

            for relative, old in sorted(previous.items()):
                if relative in handled_old or relative in current:
                    continue
                if self._is_under_any(relative, inaccessible):
                    # An unreadable subtree cannot be interpreted as empty.
                    # Publish the transition once; re-emit only after recovery.
                    if relative not in self._stale_paths:
                        await self._emit(
                            "filesystem.file_unavailable",
                            relative,
                            old.payload(),
                            freshness="stale",
                        )
                        self._stale_paths.add(relative)
                    continue
                await self._emit("filesystem.file_deleted", relative, {})
                self._stale_paths.discard(relative)

        # Preserve entries under inaccessible directories for later recheck;
        # otherwise only the confirmed current snapshot remains authoritative.
        next_known = dict(current)
        for relative, entry in previous.items():
            if relative not in current and self._is_under_any(relative, inaccessible):
                next_known[relative] = entry
        self._known = next_known

    def _collect_snapshot(self) -> tuple[dict[str, _FileEntry], list[str]]:
        assert self._root is not None
        current: dict[str, _FileEntry] = {}
        inaccessible: list[str] = []

        def on_error(error: OSError) -> None:
            filename = getattr(error, "filename", None)
            if filename:
                relative = self._relative_from_absolute(str(filename))
                if relative:
                    inaccessible.append(relative)

        for directory, dirnames, filenames in os.walk(
            self._root,
            topdown=True,
            followlinks=False,
            onerror=on_error,
        ):
            base = Path(directory)
            safe_dirs: list[str] = []
            for name in dirnames:
                candidate = base / name
                try:
                    if candidate.is_symlink():
                        continue
                    resolved = candidate.resolve(strict=True)
                    if resolved.is_relative_to(self._root):
                        safe_dirs.append(name)
                except OSError:
                    relative = self._relative_from_absolute(str(candidate))
                    if relative:
                        inaccessible.append(relative)
            dirnames[:] = safe_dirs

            for name in filenames:
                candidate = base / name
                try:
                    if candidate.is_symlink():
                        continue
                    resolved = candidate.resolve(strict=True)
                    if not resolved.is_relative_to(self._root):
                        continue
                    stat_result = candidate.stat(follow_symlinks=False)
                    if not stat_module.S_ISREG(stat_result.st_mode):
                        continue
                    relative = candidate.relative_to(self._root).as_posix()
                    current[relative] = _FileEntry(
                        relative_path=relative,
                        size_bytes=int(stat_result.st_size),
                        modified_ns=int(stat_result.st_mtime_ns),
                        device=int(getattr(stat_result, "st_dev", 0)),
                        inode=int(getattr(stat_result, "st_ino", 0)),
                    )
                except FileNotFoundError:
                    # The path changed while scanning. Keep it uncertain rather
                    # than claiming it was absent at a stable observation point.
                    relative = self._relative_from_absolute(str(candidate))
                    if relative:
                        inaccessible.append(relative)
                except OSError:
                    relative = self._relative_from_absolute(str(candidate))
                    if relative:
                        inaccessible.append(relative)
        return current, inaccessible

    def _relative_from_absolute(self, value: str) -> str | None:
        root = self._root
        if root is None or not value:
            return None
        try:
            path = Path(value).resolve(strict=False)
            relative = path.relative_to(root)
        except (OSError, ValueError):
            return None
        if not relative.parts or any(part in {".", ".."} for part in relative.parts):
            return None
        return relative.as_posix()

    @staticmethod
    def _is_under_any(relative: str, prefixes: list[str]) -> bool:
        return any(relative == prefix or relative.startswith(prefix.rstrip("/") + "/") for prefix in prefixes)

    async def _emit_rename(self, old_relative: str, new_relative: str, entry: _FileEntry) -> None:
        await self._emit(
            "filesystem.file_renamed",
            new_relative,
            {**entry.payload(), "old_relative_path": old_relative},
        )

    async def _emit(
        self,
        kind: str,
        relative_path: str,
        payload: dict[str, Any],
        *,
        freshness: str = "current",
    ) -> None:
        self._sequence += 1
        observation = Observation(
            source_id=self.adapter_id,
            subject=f"file:{relative_path}",
            kind=kind,
            payload=payload,
            observed_at=datetime.now(timezone.utc),
            sequence=self._sequence,
            verification="confirmed" if freshness == "current" else "unverified",
            permission_scope=f"sandbox:{self.root}",
            freshness=freshness,  # type: ignore[arg-type]
        )
        await self._sink(observation)
        self._observations_emitted += 1
