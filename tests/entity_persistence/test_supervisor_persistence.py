import asyncio
from datetime import datetime, timezone

import pytest

from sylphy_entity.contracts.decision import (
    Behaviour, BrainState, DriveState, Goal, GoalStatus, NoveltyRecord,
)
from sylphy_entity.contracts.events import EntityEvent
from sylphy_entity.contracts.observation import Observation
from sylphy_entity.contracts.world import FactState
from sylphy_entity.runtime.supervisor import EntitySupervisor


@pytest.mark.asyncio
async def test_supervisor_restores_entity_brain_and_world_after_clean_restart(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    lock = tmp_path / "entity.lock"
    first = EntitySupervisor(lock_path=lock, port=0, sandbox_root=sandbox, tick_interval_seconds=0.02)
    await first.start()
    entity_id = first.status()["persistence"]["entity_id"]
    observation = Observation(
        source_id="filesystem:sandbox", subject="file:note.txt", kind="filesystem.file_created",
        payload={"relative_path": "note.txt", "kind": "filesystem.file_created"},
        permission_scope=f"sandbox:{sandbox.resolve()}",
    )
    await first.publish_observation(observation)
    for _ in range(100):
        if first.status()["events"]["processed"] >= 1:
            break
        await asyncio.sleep(0.01)
    goal = Goal(
        goal_id="goal-continuity", kind="inspect_file_change", subject="file:note.txt",
        source="brain", priority=0.8, urgency=0.7, desired_outcome="inspect safely",
        target="note.txt", status=GoalStatus.ACTIVE,
        required_permissions=("OBSERVE_SANDBOX",),
        permission_scope=f"sandbox:{sandbox.resolve()}",
    )
    first.save_brain_state(
        BrainState(
            behaviour=Behaviour.PURSUING, virtual_time=42.0,
            drives=DriveState(curiosity=0.9, continuity=0.8), goals=(goal,),
            novelty=(NoveltyRecord("file:note.txt", 40.0, visit_count=2, depth=1),),
            tick_count=12,
        )
    )
    await first.shutdown()

    second = EntitySupervisor(lock_path=lock, port=0, sandbox_root=sandbox, tick_interval_seconds=0.02)
    await second.start()
    try:
        status = second.status()
        assert status["persistence"]["entity_id"] == entity_id
        assert status["persistence"]["previous_shutdown_clean"] is True
        assert status["persistence"]["boot_count"] == 2
        restored = second.brain_state_snapshot()
        assert restored["virtual_time"] == 42.0
        assert restored["drives"]["curiosity"] == 0.9
        assert restored["goals"][0]["goal_id"] == "goal-continuity"
        assert restored["novelty"][0]["visit_count"] == 2
        fact = second.world_snapshot()["facts"][0]
        assert fact["state"] == FactState.STALE.value
    finally:
        await second.shutdown()


@pytest.mark.asyncio
async def test_committed_event_after_projection_checkpoint_replays_on_restart(tmp_path):
    lock = tmp_path / "entity.lock"
    first = EntitySupervisor(lock_path=lock, port=0, tick_interval_seconds=0.02)
    await first.start()
    event = EntityEvent(
        event_type="world.observation", source="filesystem:sandbox",
        payload={
            "observation": Observation(
                source_id="filesystem:sandbox", subject="file:replayed.txt",
                kind="filesystem.file_created", payload={"relative_path": "replayed.txt"},
                permission_scope="sandbox:/test",
            ).model_dump(mode="json")
        },
    )
    # Simulate crash window: durable append completed, event has not entered the queue.
    first._event_store.append(event)
    await first.shutdown()

    second = EntitySupervisor(lock_path=lock, port=0, tick_interval_seconds=0.02)
    await second.start()
    try:
        fact = second.world_snapshot()["facts"][0]
        assert fact["subject"] == "file:replayed.txt"
        assert fact["state"] == "stale"
        assert second.status()["persistence"]["last_projected_event_sequence"] >= 1
    finally:
        await second.shutdown()


@pytest.mark.asyncio
async def test_pending_observation_row_replays_after_crash_before_world_checkpoint(tmp_path):
    lock = tmp_path / "entity.lock"
    first = EntitySupervisor(lock_path=lock, port=0, tick_interval_seconds=0.02)
    await first.start()
    observation = Observation(
        source_id="filesystem:sandbox", subject="file:pending.txt",
        kind="filesystem.file_created", payload={"relative_path": "pending.txt"},
        permission_scope="sandbox:/replay",
    )
    event = EntityEvent(
        event_type="world.observation", source=observation.source_id,
        payload={"observation": observation.model_dump(mode="json")},
    )
    appended = first._event_store.append(event)
    # Simulate the narrow crash window after observation provenance commit but
    # before World Snapshot + projected marker commit.
    assert first._event_store.append_observation(observation, appended.sequence) is True
    await first.shutdown()

    second = EntitySupervisor(lock_path=lock, port=0, tick_interval_seconds=0.02)
    await second.start()
    try:
        fact = next(f for f in second.world_snapshot()["facts"] if f["subject"] == "file:pending.txt")
        assert fact["state"] == "stale"
        with second._database.read() as db:
            projected = db.execute(
                "SELECT projected FROM observations WHERE observation_id=?",
                (observation.observation_id,),
            ).fetchone()[0]
        assert projected == 1
    finally:
        await second.shutdown()


@pytest.mark.asyncio
async def test_incomplete_dispatch_becomes_unknown_without_automatic_retry(tmp_path):
    lock = tmp_path / "entity.lock"
    first = EntitySupervisor(lock_path=lock, port=0)
    await first.start()
    from tests.entity_persistence.test_recovery import _intent
    dispatching = _intent("pending-dispatch")
    first._event_store.record_intent(dispatching)
    first._event_store.mark_intent_dispatching(dispatching)
    await first.shutdown()

    second = EntitySupervisor(lock_path=lock, port=0)
    await second.start()
    try:
        assert second.status()["persistence"]["recovery"]["unknown_intent_ids"] == ["pending-dispatch"]
        with second._database.read() as db:
            row = db.execute("SELECT status FROM intents WHERE intent_id='pending-dispatch'").fetchone()
        assert row[0] == "UNKNOWN"
        # Recovery records uncertainty and never invokes a capability handler.
        assert second._event_store.list_incomplete_intents() == ()
    finally:
        await second.shutdown()


@pytest.mark.asyncio
async def test_direct_supervisor_worker_terminal_state_is_persisted(tmp_path):
    import sys

    lock = tmp_path / "entity.lock"
    supervisor = EntitySupervisor(lock_path=lock, port=0)
    await supervisor.start()
    try:
        await supervisor.start_worker("short-lived", [sys.executable, "-c", "print('done')"])
        result = await supervisor.processes.wait("short-lived")
        assert result.return_code == 0
        with supervisor._database.read() as db:
            rows = db.execute(
                "SELECT state,return_code,ended_at FROM worker_processes WHERE metadata_json LIKE '%short-lived%'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "EXITED"
        assert rows[0][1] == 0
        assert rows[0][2] is not None
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_direct_supervisor_failed_worker_state_is_persisted(tmp_path):
    import sys

    lock = tmp_path / "entity.lock"
    supervisor = EntitySupervisor(lock_path=lock, port=0)
    await supervisor.start()
    try:
        await supervisor.start_worker(
            "failed-short-lived", [sys.executable, "-c", "raise SystemExit(17)"]
        )
        result = await supervisor.processes.wait("failed-short-lived")
        assert result.return_code == 17
        with supervisor._database.read() as db:
            row = db.execute(
                "SELECT state,return_code,ended_at FROM worker_processes "
                "WHERE metadata_json LIKE '%failed-short-lived%'"
            ).fetchone()
        assert row[0] == "FAILED"
        assert row[1] == 17
        assert row[2] is not None
    finally:
        await supervisor.shutdown()
