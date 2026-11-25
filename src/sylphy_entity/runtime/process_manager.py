"""Small subprocess supervisor for isolated Entity workers.

The manager deliberately accepts an argv sequence and never invokes a shell.
It records process creation time so a PID alone is never treated as identity.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Mapping, Sequence
import inspect

import psutil

logger = logging.getLogger(__name__)


class WorkerState(str, Enum):
    RUNNING = "running"
    EXITED = "exited"
    FAILED = "failed"
    STOPPING = "stopping"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    name: str
    pid: int
    create_time: float | None
    state: WorkerState
    return_code: int | None


@dataclass(frozen=True, slots=True)
class WorkerResult:
    name: str
    pid: int
    return_code: int
    stdout: str
    stderr: str
    state: WorkerState
    create_time: float | None = None


@dataclass
class _ManagedWorker:
    name: str
    process: asyncio.subprocess.Process
    create_time: float | None
    state: WorkerState
    monitor: asyncio.Task[WorkerResult] | None = None
    stop_requested: bool = False


class WorkerAlreadyExists(RuntimeError):
    pass


class WorkerNotFound(KeyError):
    pass


class ProcessManager:
    def __init__(
        self,
        *,
        stop_grace_seconds: float = 3.0,
        on_result: Callable[[WorkerResult], Awaitable[None] | None] | None = None,
        on_started: Callable[[WorkerSnapshot], Awaitable[None] | None] | None = None,
    ) -> None:
        if stop_grace_seconds < 0:
            raise ValueError("stop_grace_seconds cannot be negative")
        self._stop_grace_seconds = stop_grace_seconds
        self._on_result = on_result
        self._on_started = on_started
        self._workers: dict[str, _ManagedWorker] = {}
        self._closed = False

    async def start(
        self,
        name: str,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> WorkerSnapshot:
        if self._closed:
            raise RuntimeError("ProcessManager is closed")
        if not name.strip() or not argv:
            raise ValueError("name and argv are required")
        existing = self._workers.get(name)
        if existing and existing.process.returncode is None:
            raise WorkerAlreadyExists(name)

        kwargs: dict[str, object] = {
            "cwd": cwd,
            "env": dict(env) if env is not None else None,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(__import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True

        process = await asyncio.create_subprocess_exec(*argv, **kwargs)
        try:
            create_time = psutil.Process(process.pid).create_time()
        except psutil.Error:
            create_time = None
        worker = _ManagedWorker(
            name=name,
            process=process,
            create_time=create_time,
            state=WorkerState.RUNNING,
        )
        self._workers[name] = worker
        snapshot = self._snapshot(worker)
        if self._on_started is not None:
            try:
                notification = self._on_started(snapshot)
                if inspect.isawaitable(notification):
                    await notification
            except Exception:
                # Do not allow an untracked worker to continue after its durable
                # identity could not be recorded.
                worker.stop_requested = True
                worker.state = WorkerState.STOPPING
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=self._stop_grace_seconds)
                except Exception:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                self._workers.pop(name, None)
                raise
        worker.monitor = asyncio.create_task(
            self._monitor(worker), name=f"sylphy-worker-monitor:{name}"
        )
        return snapshot

    async def _monitor(self, worker: _ManagedWorker) -> WorkerResult:
        stdout_b, stderr_b = await worker.process.communicate()
        return_code = worker.process.returncode
        assert return_code is not None
        if worker.stop_requested:
            worker.state = WorkerState.CANCELLED
        else:
            worker.state = WorkerState.EXITED if return_code == 0 else WorkerState.FAILED
        result = WorkerResult(
            name=worker.name,
            pid=worker.process.pid,
            return_code=return_code,
            stdout=stdout_b.decode(errors="replace"),
            stderr=stderr_b.decode(errors="replace"),
            state=worker.state,
            create_time=worker.create_time,
        )
        if return_code != 0 and result.state is WorkerState.FAILED:
            logger.error("Entity worker %s exited with code %s", worker.name, return_code)
        # Notify the lifecycle owner from the same monitor that establishes the
        # authoritative exit result. This avoids a second observer task racing
        # shutdown and accidentally converting an unobserved failure into a
        # normal worker cancellation.
        if self._on_result is not None:
            try:
                notification = self._on_result(result)
                if inspect.isawaitable(notification):
                    await notification
            except Exception:
                logger.exception("Worker result callback failed for %s", worker.name)
        return result

    async def wait(self, name: str) -> WorkerResult:
        worker = self._workers.get(name)
        if worker is None:
            raise WorkerNotFound(name)
        assert worker.monitor is not None
        # A caller timing out/cancelling its wait must not cancel the single
        # authoritative process monitor; stop() may still need its result.
        return await asyncio.shield(worker.monitor)

    async def stop(self, name: str) -> WorkerResult:
        worker = self._workers.get(name)
        if worker is None:
            raise WorkerNotFound(name)
        if worker.process.returncode is not None:
            return await self.wait(name)

        worker.state = WorkerState.STOPPING
        worker.stop_requested = True
        self._terminate_tree(worker)
        try:
            return await asyncio.wait_for(self.wait(name), timeout=self._stop_grace_seconds)
        except asyncio.TimeoutError:
            self._kill_tree(worker)
            return await self.wait(name)

    async def forget(self, name: str) -> None:
        """Release a completed worker record after its result has been consumed."""
        worker = self._workers.get(name)
        if worker is None:
            return
        if worker.process.returncode is None:
            raise RuntimeError(f"cannot forget active worker: {name}")
        await self.wait(name)
        self._workers.pop(name, None)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        active = [w.name for w in self._workers.values() if w.process.returncode is None]
        if active:
            await asyncio.gather(*(self.stop(name) for name in active), return_exceptions=True)
        monitors = [w.monitor for w in self._workers.values() if w.monitor is not None]
        if monitors:
            await asyncio.gather(*monitors, return_exceptions=True)

    def snapshots(self) -> list[WorkerSnapshot]:
        return [self._snapshot(worker) for worker in self._workers.values()]

    def _snapshot(self, worker: _ManagedWorker) -> WorkerSnapshot:
        return WorkerSnapshot(
            name=worker.name,
            pid=worker.process.pid,
            create_time=worker.create_time,
            state=worker.state,
            return_code=worker.process.returncode,
        )

    @staticmethod
    def _same_process(pid: int, create_time: float | None) -> psutil.Process | None:
        try:
            process = psutil.Process(pid)
            if create_time is None or abs(process.create_time() - create_time) >= 1.0:
                return None
            return process
        except psutil.Error:
            return None

    def _terminate_tree(self, worker: _ManagedWorker) -> None:
        # psutil is used only to discover descendants after PID + creation-time
        # verification. The asyncio subprocess object is the authoritative
        # handle for the direct child and remains usable when psutil cannot read
        # creation time (e.g. a Windows access restriction).
        process = self._same_process(worker.process.pid, worker.create_time)
        if process is not None:
            try:
                descendants = process.children(recursive=True)
            except psutil.Error:
                descendants = []
            for child in reversed(descendants):
                try:
                    child.terminate()
                except psutil.Error:
                    pass
        if worker.process.returncode is None:
            try:
                worker.process.terminate()
            except ProcessLookupError:
                pass

    def _kill_tree(self, worker: _ManagedWorker) -> None:
        process = self._same_process(worker.process.pid, worker.create_time)
        if process is not None:
            try:
                descendants = process.children(recursive=True)
            except psutil.Error:
                descendants = []
            for child in reversed(descendants):
                try:
                    child.kill()
                except psutil.Error:
                    pass
        if worker.process.returncode is None:
            try:
                worker.process.kill()
            except ProcessLookupError:
                pass
