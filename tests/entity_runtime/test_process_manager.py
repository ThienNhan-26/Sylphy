import sys

import pytest

from sylphy_entity.runtime.process_manager import ProcessManager, WorkerState


@pytest.mark.asyncio
async def test_failed_worker_is_reported_without_raising_from_manager() -> None:
    manager = ProcessManager()
    try:
        snapshot = await manager.start(
            "failing-worker",
            [
                sys.executable,
                "-c",
                "import sys; print('worker-out'); print('worker-err', file=sys.stderr); sys.exit(7)",
            ],
        )
        assert snapshot.pid > 0
        result = await manager.wait("failing-worker")
        assert result.return_code == 7
        assert result.state is WorkerState.FAILED
        assert result.create_time == snapshot.create_time
        assert "worker-out" in result.stdout
        assert "worker-err" in result.stderr
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_manager_stops_long_running_worker() -> None:
    manager = ProcessManager(stop_grace_seconds=1.0)
    try:
        await manager.start("sleeping-worker", [sys.executable, "-c", "import time; time.sleep(30)"])
        result = await manager.stop("sleeping-worker")
        assert result.return_code is not None
        assert result.state is WorkerState.CANCELLED
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_cancelling_a_waiter_does_not_cancel_authoritative_monitor():
    import asyncio
    import sys

    from sylphy_entity.runtime.process_manager import ProcessManager, WorkerState

    manager = ProcessManager(stop_grace_seconds=0.1)
    await manager.start(
        "long-worker",
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    waiter = asyncio.create_task(manager.wait("long-worker"))
    await asyncio.sleep(0.02)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    monitor = manager._workers["long-worker"].monitor
    assert monitor is not None and not monitor.cancelled()
    result = await manager.stop("long-worker")
    assert result.state is WorkerState.CANCELLED
    await manager.close()


@pytest.mark.asyncio
async def test_process_manager_notifies_lifecycle_owner_once_on_worker_failure():
    import asyncio

    seen = []
    finished = asyncio.Event()

    async def on_result(result):
        seen.append((result.name, result.state, result.return_code))
        finished.set()

    manager = ProcessManager(on_result=on_result)
    try:
        await manager.start("callback-failure", [sys.executable, "-c", "raise SystemExit(13)"])
        await asyncio.wait_for(finished.wait(), timeout=2.0)
        result = await manager.wait("callback-failure")
        assert result.state is WorkerState.FAILED
        assert seen == [("callback-failure", WorkerState.FAILED, 13)]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_completed_worker_can_be_forgotten_but_active_worker_cannot():
    import sys

    from sylphy_entity.runtime.process_manager import ProcessManager

    manager = ProcessManager(stop_grace_seconds=0.1)
    try:
        await manager.start("brief-worker", [sys.executable, "-c", "print('done')"])
        with pytest.raises(RuntimeError, match="active worker"):
            await manager.forget("brief-worker")
        result = await manager.wait("brief-worker")
        assert result.return_code == 0
        await manager.forget("brief-worker")
        assert manager.snapshots() == []
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_direct_worker_handle_still_stops_process_when_psutil_creation_time_is_unavailable(monkeypatch):
    import psutil

    manager = ProcessManager(stop_grace_seconds=0.5)

    def inaccessible_create_time(self):
        raise psutil.AccessDenied(pid=self.pid)

    monkeypatch.setattr(psutil.Process, "create_time", inaccessible_create_time)
    try:
        snapshot = await manager.start(
            "worker-with-hidden-create-time",
            [sys.executable, "-c", "import time; time.sleep(30)"],
        )
        assert snapshot.create_time is None
        result = await manager.stop("worker-with-hidden-create-time")
        assert result.state is WorkerState.CANCELLED
        assert result.return_code is not None
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_start_callback_runs_before_worker_monitor_and_callback_failure_stops_child():
    import asyncio

    from sylphy_entity.runtime.process_manager import ProcessManager

    seen = []

    async def on_started(snapshot):
        assert snapshot.state is WorkerState.RUNNING
        assert snapshot.pid > 0
        seen.append(snapshot.pid)

    manager = ProcessManager(on_started=on_started, stop_grace_seconds=0.2)
    try:
        snapshot = await manager.start(
            "callback-order", [sys.executable, "-c", "import time; time.sleep(0.1)"]
        )
        result = await manager.wait("callback-order")
        assert seen == [snapshot.pid]
        assert result.return_code == 0
    finally:
        await manager.close()

    async def reject_start(snapshot):
        raise RuntimeError("cannot durably record worker identity")

    rejected = ProcessManager(on_started=reject_start, stop_grace_seconds=0.2)
    with pytest.raises(RuntimeError, match="durably record"):
        await rejected.start(
            "must-not-escape", [sys.executable, "-c", "import time; time.sleep(30)"]
        )
    assert rejected.snapshots() == []
    await rejected.close()
