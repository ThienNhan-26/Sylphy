"""Portable non-blocking single-instance lock for the Entity Daemon."""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO


class InstanceAlreadyRunning(RuntimeError):
    """Raised when another Entity Daemon owns the runtime lock."""


class InstanceLock:
    """Hold an OS-level byte/file lock for the lifetime of the daemon."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._file: IO[bytes] | None = None

    @property
    def acquired(self) -> bool:
        return self._file is not None

    def acquire(self) -> None:
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise InstanceAlreadyRunning(
                        f"Another SYLPHY Entity Daemon owns {self.path}"
                    ) from exc
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise InstanceAlreadyRunning(
                        f"Another SYLPHY Entity Daemon owns {self.path}"
                    ) from exc

            # Keep the locked byte at offset zero; diagnostic text starts at 1.
            handle.seek(1)
            handle.truncate()
            handle.write(f"pid={os.getpid()}\n".encode("ascii"))
            handle.flush()
            self._file = handle
        except Exception:
            handle.close()
            raise

    def release(self) -> None:
        handle = self._file
        if handle is None:
            return
        self._file = None
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()
