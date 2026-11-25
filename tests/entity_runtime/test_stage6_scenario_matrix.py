from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psutil
import pytest

from sylphy_entity.brain.core import EntityBrain, inspection_intent_id
from sylphy_entity.contracts.decision import BrainState, GoalFeedback, GoalOutcome, GoalStatus
from sylphy_entity.contracts.execution import (
    ExecutionResult,
    ExecutionStatus,
    PermissionClass,
)
from sylphy_entity.contracts.observation import Observation
from sylphy_entity.execution.dispatcher import IntentDispatcher
from sylphy_entity.execution.intent_router import IntentRouter
from sylphy_entity.execution.permissions import AuthorizedTarget, PermissionPolicy
from sylphy_entity.execution.resource_arbiter import ResourceArbiter
from sylphy_entity.runtime.supervisor import EntitySupervisor
from sylphy_entity.world import WorldModel, WorldProjector
from sylphy_entity.world.adapters.simulated import SimulatedFilesystemAdapter
from sylphy_entity.contracts.world import FactState


async def _wait_for(predicate, *, timeout: float = 5.0, interval: float = 0.025):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError("condition was not reached before timeout")


class _SimulatedPermissionPolicy:
    """Test-only permission boundary over the simulated filesystem state."""

    scope = "simulated:sandbox"

    def __init__(self, adapter: SimulatedFilesystemAdapter) -> None:
        self.adapter = adapter
        self.revoked = False

    def authorize(self, intent):
        if self.revoked:
            raise PermissionError("simulated scope revoked")
        if intent.permission_scope != self.scope:
            raise PermissionError("wrong simulated scope")
        if intent.permission_class is not PermissionClass.OBSERVE_SANDBOX:
            raise PermissionError("wrong simulated permission")
        metadata = self.adapter.metadata_for(intent.target)
        if metadata is None:
            raise PermissionError("simulated file disappeared before execution")
        root = Path("/virtual/sylphy-sandbox")
        return AuthorizedTarget(
            root=root,
            path=root / intent.target,
            relative_path=intent.target,
            permission_class=PermissionClass.OBSERVE_SANDBOX,
            permission_scope=self.scope,
            size_bytes=int(metadata["size_bytes"]),
        )

    def revoke_scope(self, scope: str) -> None:
        if scope != self.scope:
            raise PermissionError("wrong simulated scope")
        self.revoked = True

    def restore_scope(self, scope: str) -> None:
        if scope != self.scope:
            raise PermissionError("wrong simulated scope")
        self.revoked = False


@pytest.mark.asyncio
async def test_simulator_runs_full_observation_to_evidence_feedback_loop() -> None:
    world = WorldModel()
    projector = WorldProjector(world)
    brain = EntityBrain()
    state = BrainState()
    adapter: SimulatedFilesystemAdapter

    async def sink(observation: Observation) -> None:
        projector.apply(observation)

    adapter = SimulatedFilesystemAdapter(sink)
    policy = _SimulatedPermissionPolicy(adapter)
    executed: list[str] = []

    async def inspect_metadata(intent, target):
        metadata = adapter.metadata_for(intent.target)
        if metadata is None:
            return ExecutionResult.immediate(
                intent.intent_id, ExecutionStatus.FAILED, error="simulated file disappeared"
            )
        material = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        evidence = "sha256:" + hashlib.sha256(material).hexdigest()
        executed.append(intent.target)
        return ExecutionResult.immediate(
            intent.intent_id,
            ExecutionStatus.SUCCEEDED,
            actual_result={
                "relative_path": intent.target,
                "size_bytes": int(metadata["size_bytes"]),
                "modified_ns": int(metadata["modified_ns"]),
            },
            evidence_ids=(evidence,),
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,  # type: ignore[arg-type]
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": inspect_metadata},
    )
    router = IntentRouter()

    async def cycle(dt: float = 0.1):
        nonlocal state
        frame = brain.step(dt, world.snapshot(), state)
        state = frame.next_state
        if not frame.intent_proposals:
            return frame, None
        proposal = frame.intent_proposals[0]
        intent = router.route(proposal)
        result = await dispatcher.execute(intent)
        if result.status is ExecutionStatus.SUCCEEDED:
            state = brain.step(
                0.0,
                world.snapshot(),
                state,
                feedback=(GoalFeedback(
                    goal_id=proposal.goal_id,
                    outcome=GoalOutcome.SUCCEEDED,
                    evidence_id=result.evidence_ids[0],
                ),),
            ).next_state
        return frame, result

    await adapter.start()
    try:
        first_observation = await adapter.discover("docs/proof.txt", size=10, modified_ns=100)
        first_frame, first_result = await cycle()
        assert first_frame.intent_proposals
        assert first_result is not None and first_result.status is ExecutionStatus.SUCCEEDED
        assert executed == ["docs/proof.txt"]
        assert first_frame.intent_proposals[0].source_observation_id == first_observation.observation_id
        first_goal = next(g for g in state.goals if g.goal_id == first_frame.intent_proposals[0].goal_id)
        assert first_goal.status is GoalStatus.COMPLETED
        assert first_goal.completion_evidence == first_result.evidence_ids[:1]

        # A duplicate watcher notification with the same file revision does
        # not create another goal attempt or duplicate side effect.
        await adapter.modify("docs/proof.txt", size=10, modified_ns=100)
        duplicate_frame, duplicate_result = await cycle()
        assert not duplicate_frame.intent_proposals
        assert duplicate_result is None
        assert executed == ["docs/proof.txt"]

        # Editing the same path creates a new revision and therefore a new
        # novelty candidate; novelty is not permanently suppressed by path.
        await adapter.modify("docs/proof.txt", size=14, modified_ns=200)
        edit_frame, edit_result = await cycle()
        assert edit_frame.intent_proposals
        assert edit_result is not None and edit_result.status is ExecutionStatus.SUCCEEDED
        assert executed == ["docs/proof.txt", "docs/proof.txt"]

        # Rename moves the known fact to the new subject; the old path becomes
        # UNKNOWN and the new path is inspected at most once.
        await adapter.rename("docs/proof.txt", "docs/renamed.txt")
        rename_frame, rename_result = await cycle()
        assert rename_frame.intent_proposals
        assert rename_frame.intent_proposals[0].target == "docs/renamed.txt"
        assert rename_result is not None and rename_result.status is ExecutionStatus.SUCCEEDED
        assert world.get_fact("file:docs/proof.txt").state is FactState.UNKNOWN
        assert executed == ["docs/proof.txt", "docs/proof.txt", "docs/renamed.txt"]

        # Deletion updates World to UNKNOWN and cannot produce an inspection.
        await adapter.delete("docs/renamed.txt")
        delete_frame, delete_result = await cycle()
        assert not delete_frame.intent_proposals
        assert delete_result is None
        assert world.get_fact("file:docs/renamed.txt").state is FactState.UNKNOWN
        assert len(executed) == 3
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_live_loop_inspects_create_edit_rename_and_stops_after_delete(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    supervisor = EntitySupervisor(
        lock_path=runtime / "daemon.lock",
        port=0,
        tick_interval_seconds=0.05,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.1,
        autonomy_enabled=True,
    )
    await supervisor.start()
    try:
        original = sandbox / "proof.txt"
        original.write_text("v1", encoding="utf-8")
        await _wait_for(lambda: len(supervisor.status()["autonomy"]["history"]) >= 1)
        first = supervisor.status()["autonomy"]["history"][0]
        assert first["status"] == "succeeded"
        assert first["source_observation_id"]
        first_notice = supervisor.status()["autonomy"]["notices"][-1]
        assert first_notice["source_observation_id"] == first["source_observation_id"]
        assert first_notice["reason"]

        original.write_text("version two is longer", encoding="utf-8")
        stamp = time.time_ns() + 1_000_000
        os.utime(original, ns=(stamp, stamp))
        await _wait_for(lambda: len(supervisor.status()["autonomy"]["history"]) >= 2)
        edit = supervisor.status()["autonomy"]["history"][1]
        assert edit["target"] == "proof.txt"
        assert edit["intent_id"] != first["intent_id"]

        renamed = sandbox / "renamed.txt"
        original.rename(renamed)
        await _wait_for(lambda: any(
            item["target"] == "renamed.txt" and item["status"] == "succeeded"
            for item in supervisor.status()["autonomy"]["history"]
        ))
        history_after_rename = len(supervisor.status()["autonomy"]["history"])

        renamed.unlink()
        await _wait_for(lambda: any(
            fact["subject"] == "file:renamed.txt" and fact["state"] == "unknown"
            for fact in supervisor.world_snapshot()["facts"]
        ))
        await asyncio.sleep(0.35)
        assert len(supervisor.status()["autonomy"]["history"]) == history_after_rename
        assert all(item["status"] == "succeeded" for item in supervisor.status()["autonomy"]["history"])
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_periodic_reconciliation_recovers_a_dropped_filesystem_wakeup(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    supervisor = EntitySupervisor(
        lock_path=runtime / "daemon.lock",
        port=0,
        tick_interval_seconds=0.05,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.12,
        autonomy_enabled=True,
    )
    await supervisor.start()
    try:
        assert supervisor._filesystem_adapter is not None
        # Simulate losing every Watchdog wake-up. Periodic reconciliation must
        # still notice the file from the authoritative filesystem snapshot.
        supervisor._filesystem_adapter._signal_change = lambda: None  # type: ignore[method-assign]
        (sandbox / "event_lost.txt").write_text("reconciled", encoding="utf-8")
        await _wait_for(lambda: any(
            item["target"] == "event_lost.txt" and item["status"] == "succeeded"
            for item in supervisor.status()["autonomy"]["history"]
        ), timeout=4.0)
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_file_disappearing_between_brain_and_dispatch_is_not_reported_success(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    target = sandbox / "vanishing.txt"
    target.write_text("disappear before inspect", encoding="utf-8")
    supervisor = EntitySupervisor(
        lock_path=runtime / "daemon.lock",
        port=0,
        tick_interval_seconds=10.0,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.1,
        autonomy_enabled=True,
    )
    await supervisor.start()
    try:
        await _wait_for(lambda: supervisor.world_snapshot()["facts"])
        dispatcher = supervisor._dispatcher
        assert dispatcher is not None
        original_execute = dispatcher.execute

        async def remove_then_execute(intent):
            target.unlink(missing_ok=True)
            return await original_execute(intent)

        dispatcher.execute = remove_then_execute  # type: ignore[method-assign]
        await supervisor._handle_tick(0.1)
        history = supervisor.status()["autonomy"]["history"]
        assert len(history) == 1
        assert history[0]["status"] == "rejected"
        assert not history[0]["evidence_ids"]
        assert supervisor.processes.snapshots() == []
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_permission_revocation_api_blocks_autonomous_intent(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    target = sandbox / "revoked.txt"
    target.write_text("not inspected after revoke", encoding="utf-8")
    supervisor = EntitySupervisor(
        lock_path=runtime / "daemon.lock",
        port=0,
        tick_interval_seconds=10.0,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.1,
        autonomy_enabled=True,
    )
    await supervisor.start()
    try:
        await _wait_for(lambda: supervisor.world_snapshot()["facts"])
        assert supervisor._control_token
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{supervisor.api_port}") as client:
            unauthorized = await client.post("/permissions/revoke", json={})
            assert unauthorized.status_code == 401
            response = await client.post(
                "/permissions/revoke",
                headers={"X-Sylphy-Control-Token": supervisor._control_token},
                json={"scope": supervisor._permission_policy.scope},
            )
            assert response.status_code == 200
            assert response.json()["revoked"] is True
        await supervisor._handle_tick(0.1)
        history = supervisor.status()["autonomy"]["history"]
        assert len(history) == 1 and history[0]["status"] == "rejected"
        assert not history[0]["evidence_ids"]
        assert supervisor._permission_policy.is_revoked(supervisor._permission_policy.scope)
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_permission_revocation_cancels_active_autonomous_execution(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    (sandbox / "revoke-active.txt").write_text("revoke while running", encoding="utf-8")
    supervisor = EntitySupervisor(
        lock_path=runtime / "daemon.lock",
        port=0,
        tick_interval_seconds=10.0,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.1,
        autonomy_enabled=True,
    )
    await supervisor.start()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    try:
        await _wait_for(lambda: supervisor.world_snapshot()["facts"])
        assert supervisor._permission_policy is not None

        async def slow_handler(intent, target):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        supervisor._dispatcher = IntentDispatcher(
            permission_policy=supervisor._permission_policy,
            resource_arbiter=supervisor._resource_arbiter,
            handlers={"inspect_file_metadata": slow_handler},
            journal=supervisor._event_store,
        )
        tick = asyncio.create_task(supervisor._handle_tick(0.1))
        await asyncio.wait_for(started.wait(), timeout=3.0)
        result = await supervisor.revoke_permission(supervisor._permission_policy.scope)
        await asyncio.wait_for(tick, timeout=3.0)
        assert result["revoked"] is True
        assert cancelled.is_set()
        assert result["cancelled_intents"]
        assert supervisor.status()["autonomy"]["history"][-1]["status"] == "cancelled"
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_user_pause_interrupts_active_autonomous_execution(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    (sandbox / "interrupt.txt").write_text("pause me", encoding="utf-8")
    supervisor = EntitySupervisor(
        lock_path=runtime / "daemon.lock",
        port=0,
        tick_interval_seconds=10.0,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.1,
        autonomy_enabled=True,
    )
    await supervisor.start()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    try:
        await _wait_for(lambda: supervisor.world_snapshot()["facts"])
        assert supervisor._permission_policy is not None
        assert supervisor._boot_info is not None

        async def slow_handler(intent, target):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        supervisor._dispatcher = IntentDispatcher(
            permission_policy=supervisor._permission_policy,
            resource_arbiter=supervisor._resource_arbiter,
            handlers={"inspect_file_metadata": slow_handler},
            journal=supervisor._event_store,
        )
        tick = asyncio.create_task(supervisor._handle_tick(0.1))
        await asyncio.wait_for(started.wait(), timeout=3.0)
        result = await supervisor.pause_autonomy()
        await asyncio.wait_for(tick, timeout=3.0)
        assert result["paused"] is True
        assert cancelled.is_set()
        status = supervisor.status()["autonomy"]
        assert status["enabled"] is False and status["paused"] is True
        assert status["history"][-1]["status"] == "cancelled"
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_worker_process_crash_is_reported_without_killing_entity(tmp_path: Path, monkeypatch) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    (sandbox / "worker-crash.txt").write_text("crash worker", encoding="utf-8")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")  # Stage 6 must not depend on a model.
    supervisor = EntitySupervisor(
        lock_path=runtime / "daemon.lock",
        port=0,
        tick_interval_seconds=10.0,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.1,
        autonomy_enabled=True,
    )
    await supervisor.start()
    worker_results = []
    try:
        await _wait_for(lambda: supervisor.world_snapshot()["facts"])
        assert supervisor._permission_policy is not None
        assert supervisor._boot_info is not None

        async def crashing_worker(intent, target):
            name = f"intent-{intent.intent_id}"
            snapshot = await supervisor.processes.start(
                name,
                [sys.executable, "-c", "import sys; sys.stderr.write('stage6-worker-crash'); sys.exit(23)"],
                cwd=str(target.root),
            )
            worker = await supervisor.processes.wait(name)
            worker_results.append(worker)
            digest = hashlib.sha256((worker.stdout + "\n" + worker.stderr).encode()).hexdigest()
            return ExecutionResult(
                intent_id=intent.intent_id,
                status=ExecutionStatus.FAILED,
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                actual_result={"return_code": worker.return_code},
                evidence_ids=(f"worker-log:sha256:{digest}",),
                error=f"worker exited with {worker.state.value}",
                worker_pid=worker.pid,
                worker_create_time=worker.create_time,
                worker_return_code=worker.return_code,
            )

        supervisor._dispatcher = IntentDispatcher(
            permission_policy=supervisor._permission_policy,
            resource_arbiter=supervisor._resource_arbiter,
            handlers={"inspect_file_metadata": crashing_worker},
            journal=supervisor._event_store,
        )
        await supervisor._handle_tick(0.1)
        status = supervisor.status()
        assert status["lifecycle"] == "degraded"
        assert status["autonomy"]["history"][-1]["status"] == "failed"
        assert worker_results and worker_results[0].return_code == 23
        assert "stage6-worker-crash" in worker_results[0].stderr
        assert supervisor._event_loop.running
        assert supervisor._api_task is not None and not supervisor._api_task.done()
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_daemon_restart_reconciles_unknown_dispatch_and_never_replays_it(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    (sandbox / "inflight.txt").write_text("side effect must not repeat", encoding="utf-8")
    lock_path = runtime / "daemon.lock"
    first = EntitySupervisor(
        lock_path=lock_path,
        port=0,
        tick_interval_seconds=10.0,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.1,
        autonomy_enabled=False,
    )
    process = None
    saved_intent = None
    try:
        await first.start()
        await _wait_for(lambda: first.world_snapshot()["facts"])
        frame = first._brain.step(0.1, first._world_model.snapshot(), first._brain_state)
        assert frame.intent_proposals
        first.save_brain_state(frame.next_state)
        saved_intent = first._intent_router.route(frame.intent_proposals[0])
        first._event_store.record_intent(saved_intent)
        first._event_store.mark_intent_dispatching(saved_intent)

        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        create_time = psutil.Process(process.pid).create_time()
        first._event_store.record_worker_started(
            intent_id=saved_intent.intent_id,
            pid=process.pid,
            create_time=create_time,
            daemon_instance_id=first._boot_info.instance_id,
            worker_name=f"intent-{saved_intent.intent_id}",
        )

        # Abrupt-stop simulation: close ingress/adapters/database without ever
        # writing the clean-shutdown marker. The tracked child remains alive.
        first._api_server.should_exit = True
        if first._api_task is not None:
            await asyncio.wait_for(first._api_task, timeout=3.0)
        if first._filesystem_adapter is not None:
            await first._filesystem_adapter.stop()
        await first._event_loop.stop()
        await first.processes.close()
        first._runtime_store.close()
        first._database.close()
        first._remove_control_token()
        first._lock.release()

        second = EntitySupervisor(
            lock_path=lock_path,
            port=0,
            tick_interval_seconds=0.05,
            sandbox_root=sandbox,
            reconcile_interval_seconds=0.1,
            autonomy_enabled=True,
        )
        await second.start()
        try:
            report = second.status()["persistence"]["recovery"]
            assert saved_intent.intent_id in report["unknown_intent_ids"]
            assert process.poll() is not None or not psutil.pid_exists(process.pid)
            blocked = await second._dispatcher.execute(saved_intent)
            assert blocked.status is ExecutionStatus.UNKNOWN
            await asyncio.sleep(0.35)
            assert second.status()["autonomy"]["history"] == []
        finally:
            await second.shutdown()
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
        if first._database.is_open:
            await first.shutdown()


@pytest.mark.asyncio
async def test_real_daemon_process_crash_after_dispatch_does_not_replay_intent(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    (sandbox / "process-crash.txt").write_text("crash boundary", encoding="utf-8")
    child_script = r'''import asyncio
import os
import sys
from pathlib import Path
from sylphy_entity.runtime.supervisor import EntitySupervisor
from sylphy_entity.execution.dispatcher import IntentDispatcher

async def run():
    sandbox = Path(sys.argv[1])
    runtime = Path(sys.argv[2])
    supervisor = EntitySupervisor(
        lock_path=runtime / "daemon.lock", port=0, tick_interval_seconds=0.05,
        sandbox_root=sandbox, reconcile_interval_seconds=0.1, autonomy_enabled=False,
    )
    await supervisor.start()
    deadline = asyncio.get_running_loop().time() + 5.0
    while not supervisor._world_model.snapshot().facts:
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("filesystem observation was not projected before crash test")
        await asyncio.sleep(0.02)

    # Generate and persist the real Brain Goal/Intent before dispatch. This is
    # the same decision path used by the autonomous Daemon tick.
    frame = supervisor._brain.step(
        0.1, supervisor._world_model.snapshot(), supervisor._brain_state
    )
    assert frame.intent_proposals, "Brain did not propose an inspection Intent"
    supervisor.save_brain_state(frame.next_state)
    intent = supervisor._intent_router.route(frame.intent_proposals[0])

    async def crash_after_dispatch(intent, target):
        print("CRASH_AFTER_DISPATCH:" + intent.intent_id, flush=True)
        os._exit(73)

    supervisor._dispatcher = IntentDispatcher(
        permission_policy=supervisor._permission_policy,
        resource_arbiter=supervisor._resource_arbiter,
        handlers={"inspect_file_metadata": crash_after_dispatch},
        journal=supervisor._event_store,
    )
    await supervisor._dispatcher.execute(intent)

asyncio.run(run())
'''

    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    source_root = str(repo_root / "src")
    env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
    child = subprocess.Popen(
        [sys.executable, "-c", child_script, str(sandbox), str(runtime)],
        cwd=repo_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    output, _ = child.communicate(timeout=20.0)
    assert child.returncode == 73, output
    assert "CRASH_AFTER_DISPATCH:intent-" in output, output

    recovered = EntitySupervisor(
        lock_path=runtime / "daemon.lock", port=0, tick_interval_seconds=0.05,
        sandbox_root=sandbox, reconcile_interval_seconds=0.1, autonomy_enabled=True,
    )
    await recovered.start()
    try:
        await _wait_for(lambda: recovered.world_snapshot()["facts"])
        recovery = recovered.status()["persistence"]["recovery"]
        assert recovery["unknown_intent_ids"]
        assert recovered.status()["brain"]["unknown_goals"]
        assert recovered.status()["autonomy"]["recovery_blocked_targets"] == ["process-crash.txt"]
        await asyncio.sleep(0.45)
        # The exact file revision associated with an UNKNOWN dispatch must not
        # be inspected again merely because startup reconciliation re-observed it.
        assert recovered.status()["autonomy"]["history"] == []
        assert recovered.status()["lifecycle"] == "running"
        assert recovered._event_loop.running

        # A genuinely new revision is new work, not a replay of the unresolved
        # pre-crash attempt. Its observation durably supersedes the quarantine.
        (sandbox / "process-crash.txt").write_text(
            "a distinct post-recovery file revision", encoding="utf-8"
        )
        await _wait_for(lambda: len(recovered.status()["autonomy"]["history"]) == 1)
        history = recovered.status()["autonomy"]["history"]
        assert history[0]["status"] == "succeeded"
        assert recovered.status()["autonomy"]["recovery_blocked_targets"] == []
        recovered_goal = next(
            goal for goal in recovered._brain_state.goals if goal.goal_id == history[0]["goal_id"]
        )
        assert recovered_goal.status is GoalStatus.COMPLETED
        assert recovered_goal.attempt_count == 2
        assert recovered.status()["brain"]["unknown_goals"] == []
    finally:
        await recovered.shutdown()

    # Superseding the UNKNOWN target is durable: another restart must not
    # quarantine the newer revision again.
    next_daemon = EntitySupervisor(
        lock_path=runtime / "daemon.lock", port=0, tick_interval_seconds=0.05,
        sandbox_root=sandbox, reconcile_interval_seconds=0.1, autonomy_enabled=True,
    )
    await next_daemon.start()
    try:
        await _wait_for(lambda: next_daemon.world_snapshot()["facts"])
        assert next_daemon.status()["autonomy"]["recovery_blocked_targets"] == []
    finally:
        await next_daemon.shutdown()


@pytest.mark.asyncio
async def test_verified_success_is_applied_to_goal_after_restart_before_next_brain_tick(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    (sandbox / "success-window.txt").write_text("verified", encoding="utf-8")
    lock_path = runtime / "daemon.lock"
    first = EntitySupervisor(
        lock_path=lock_path,
        port=0,
        tick_interval_seconds=10.0,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.1,
    )
    try:
        await first.start()
        await _wait_for(lambda: first.world_snapshot()["facts"])
        frame = first._brain.step(0.1, first._world_model.snapshot(), first._brain_state)
        proposal = frame.intent_proposals[0]
        first.save_brain_state(frame.next_state)
        intent = first._intent_router.route(proposal)
        first._event_store.record_intent(intent)
        first._event_store.mark_intent_dispatching(intent)
        evidence = "sha256:" + hashlib.sha256(b"verified-stage6-result").hexdigest()
        result = ExecutionResult.immediate(
            intent.intent_id, ExecutionStatus.SUCCEEDED,
            actual_result={"relative_path": intent.target, "size_bytes": 8},
            evidence_ids=(evidence,),
        )
        first._event_store.finish_execution(intent, result)

        # The Brain is deliberately still ACTIVE: simulate a crash after the
        # execution result commit but before result feedback is applied.
        first._api_server.should_exit = True
        if first._api_task is not None:
            await asyncio.wait_for(first._api_task, timeout=3.0)
        if first._filesystem_adapter is not None:
            await first._filesystem_adapter.stop()
        await first._event_loop.stop()
        await first.processes.close()
        first._runtime_store.close()
        first._database.close()
        first._remove_control_token()
        first._lock.release()

        second = EntitySupervisor(
            lock_path=lock_path,
            port=0,
            tick_interval_seconds=10.0,
            sandbox_root=sandbox,
            reconcile_interval_seconds=0.1,
        )
        await second.start()
        try:
            goal = next(g for g in second._brain_state.goals if g.goal_id == intent.goal_id)
            assert goal.status is GoalStatus.COMPLETED
            assert evidence in goal.completion_evidence
            assert second.status()["persistence"]["state_revision"] > 0
        finally:
            await second.shutdown()
    finally:
        if first._database.is_open:
            await first.shutdown()


@pytest.mark.asyncio
async def test_idle_loop_does_not_repeat_completed_work_and_does_not_need_ollama(tmp_path: Path, monkeypatch) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    (sandbox / "idle.txt").write_text("single inspect", encoding="utf-8")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")
    supervisor = EntitySupervisor(
        lock_path=runtime / "daemon.lock",
        port=0,
        tick_interval_seconds=0.05,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.1,
        autonomy_enabled=True,
    )
    await supervisor.start()
    try:
        await _wait_for(lambda: len(supervisor.status()["autonomy"]["history"]) == 1)
        await asyncio.sleep(0.5)
        status = supervisor.status()
        assert len(status["autonomy"]["history"]) == 1
        assert status["autonomy"]["history"][0]["status"] == "succeeded"
        assert status["brain"]["tick_count"] >= 2
        assert status["autonomy"]["enabled"] is True
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_resume_cannot_enable_autonomy_without_daemon_opt_in(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    supervisor = EntitySupervisor(
        lock_path=runtime / "daemon.lock", port=0, sandbox_root=sandbox,
        tick_interval_seconds=10.0, autonomy_enabled=False,
    )
    await supervisor.start()
    try:
        await supervisor.pause_autonomy()
        resumed = await supervisor.resume_autonomy()
        assert resumed["paused"] is False
        assert resumed["enabled"] is False
        assert resumed["opt_in_required"] is True
        assert supervisor.status()["autonomy"]["enabled"] is False
    finally:
        await supervisor.shutdown()
