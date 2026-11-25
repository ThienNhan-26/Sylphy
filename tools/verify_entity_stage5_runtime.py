"""Process-level Stage 5 persistence/recovery acceptance for Windows and Linux."""

from __future__ import annotations

from pathlib import Path
import atexit
import subprocess
import sys
import tempfile
import time

import psutil

from sylphy_entity.contracts.decision import (
    Behaviour,
    BrainState,
    DriveState,
    Goal,
    GoalStatus,
    NoveltyRecord,
)
from sylphy_entity.contracts.events import EntityEvent
from sylphy_entity.contracts.execution import (
    CancellationPolicy,
    Intent,
    IntentOrigin,
    PermissionClass,
    ResourceMode,
    ResourceRequirement,
)
from sylphy_entity.contracts.observation import Observation
from sylphy_entity.contracts.world import FactState
from sylphy_entity.persistence.database import EntityDatabase
from sylphy_entity.persistence.event_store import EventStore
from sylphy_entity.persistence.recovery import RecoveryManager
from sylphy_entity.persistence.runtime_store import RuntimeStore
from sylphy_entity.persistence.state_repository import StateRepository
from sylphy_entity.world.model import WorldModel
from sylphy_entity.world.projector import WorldProjector


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    child: subprocess.Popen | None = None

    def cleanup_child() -> None:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)

    atexit.register(cleanup_child)
    with tempfile.TemporaryDirectory(prefix="sylphy-entity-stage5-") as raw:
        root = Path(raw)
        database_path = root / "entity_runtime.sqlite3"

        database = EntityDatabase(database_path)
        database.open()
        runtime = RuntimeStore(database=database)
        first_boot = runtime.begin_runtime()
        event_store = EventStore(database)
        repository = StateRepository(database)
        entity_id = repository.entity_id

        # Persist a real Brain state containing an active goal, drives and novelty.
        goal = Goal(
            goal_id="stage5-verify-goal",
            kind="inspect_file_change",
            subject="file:stage5-note.txt",
            source="brain",
            priority=0.8,
            urgency=0.7,
            desired_outcome="inspect safely",
            target="stage5-note.txt",
            status=GoalStatus.ACTIVE,
            required_permissions=("OBSERVE_SANDBOX",),
            permission_scope="sandbox:stage5-verification",
        )
        brain_state = BrainState(
            behaviour=Behaviour.PURSUING,
            virtual_time=123.5,
            drives=DriveState(curiosity=0.91, continuity=0.83),
            goals=(goal,),
            novelty=(NoveltyRecord("file:stage5-note.txt", 120.0, visit_count=3, depth=2),),
            tick_count=29,
        )
        repository.save_brain_state(brain_state, metadata={"acceptance": "stage5"})

        # Persist a current World fact. On the next boot it must be downgraded
        # to STALE until the environment is observed again.
        observation = Observation(
            source_id="filesystem:sandbox",
            subject="file:stage5-note.txt",
            kind="filesystem.file_created",
            payload={"relative_path": "stage5-note.txt", "size": 12},
            permission_scope="sandbox:stage5-verification",
        )
        model = WorldModel()
        WorldProjector(model).apply(observation)
        repository.save_world_snapshot(model.snapshot(), event_sequence=0)
        event = EntityEvent(
            event_type="world.observation",
            source=observation.source_id,
            payload={"observation": observation.model_dump(mode="json")},
        )
        appended = event_store.append(event)
        needs_projection = event_store.append_observation(observation, appended.sequence)
        _assert(needs_projection, "new observation should remain pending until its snapshot is committed")
        # Commit snapshot and projection flag atomically, as the Daemon does.
        repository.save_world_snapshot(
            model.snapshot(), event_sequence=appended.sequence,
            projected_observation_id=observation.observation_id,
        )

        # Persist an Intent before its side-effect boundary, then emulate a
        # process that died after dispatch began but before a verified result.
        now = time.monotonic()
        intent = Intent(
            intent_id="stage5-interrupted-intent",
            goal_id=goal.goal_id,
            capability="inspect_file_metadata",
            target="stage5-note.txt",
            arguments=(("relative_path", "stage5-note.txt"),),
            reason="Stage 5 recovery acceptance",
            preconditions=("target_exists",),
            permission_class=PermissionClass.OBSERVE_SANDBOX,
            permission_scope="sandbox:stage5-verification",
            resources=(ResourceRequirement("filesystem:stage5-verification:stage5-note.txt", ResourceMode.SHARED),),
            priority=0.7,
            expected_outcome="metadata evidence",
            idempotency_key="stage5-interrupted-idempotency-key",
            created_at_monotonic=now,
            deadline_at_monotonic=None,
            timeout_seconds=10.0,
            cancellation_policy=CancellationPolicy.CANCEL_ON_TAKEOVER,
            origin=IntentOrigin.AUTONOMOUS,
        )
        event_store.record_intent(intent)
        event_store.mark_intent_dispatching(intent)

        # A real process outside the new Daemon's in-memory ProcessManager
        # simulates a child that outlived a crashed Daemon. Its exact identity
        # is journaled and RecoveryManager must verify before terminating it.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        created = psutil.Process(child.pid).create_time()
        event_store.record_worker_started(
            intent_id=intent.intent_id,
            pid=child.pid,
            create_time=created,
            daemon_instance_id=first_boot.instance_id,
            worker_name="stage5-orphan-worker",
        )
        repository.save_permission(
            "sandbox-read:stage5-verification", "READ_SANDBOX",
            "sandbox:stage5-verification", state="REVOKED",
        )

        # Deliberately do not mark clean shutdown: this is the persisted state
        # left by an abruptly terminated Daemon.
        runtime.close()
        database.close()

        reopened = EntityDatabase(database_path)
        reopened.open()
        next_runtime = RuntimeStore(database=reopened)
        second_boot = next_runtime.begin_runtime()
        next_repository = StateRepository(reopened)
        next_events = EventStore(reopened)
        _assert(second_boot.previous_shutdown_clean is False, "unclean shutdown marker was not retained")
        _assert(next_repository.entity_id == entity_id, "Entity identity changed after restart")

        restored = next_repository.load_brain_state(restore_transients=True)
        _assert(restored is not None, "Brain state did not restore")
        _assert(restored.virtual_time == 123.5, "Brain virtual time did not restore")
        _assert(restored.drives.curiosity == 0.91, "Brain drive did not restore")
        _assert(restored.goals and restored.goals[0].goal_id == goal.goal_id, "active Goal did not restore")
        _assert(restored.novelty and restored.novelty[0].visit_count == 3, "Novelty history did not restore")
        print("PASS: Entity identity, Brain drives, Goal and Novelty survived database restart")

        world_snapshot, checkpoint = next_repository.load_latest_world_snapshot()
        _assert(world_snapshot is not None and checkpoint == appended.sequence, "World checkpoint did not restore")
        restored_world = WorldModel()
        restored_world.restore(world_snapshot)
        restored_world.mark_all_stale()
        fact = restored_world.snapshot().fact("file:stage5-note.txt")
        _assert(fact is not None and fact.state is FactState.STALE, "pre-restart World fact was trusted as current")
        print("PASS: World Snapshot restored and downgraded to STALE pending re-observation")

        recovery = RecoveryManager(reopened, next_events).recover(second_boot.previous_shutdown_clean)
        _assert(intent.intent_id in recovery.unknown_intent_ids, "in-flight Intent was not classified UNKNOWN")
        _assert(child.poll() is not None, "verified orphan worker was not terminated")
        _assert(recovery.terminated_worker_ids, "orphan worker identity was not recorded as terminated")
        print("PASS: in-flight Intent classified UNKNOWN; verified orphan process terminated")

        with reopened.read() as db:
            intent_status = db.execute(
                "SELECT status FROM intents WHERE intent_id=?", (intent.intent_id,)
            ).fetchone()[0]
            permission_state = db.execute(
                "SELECT state FROM permissions WHERE permission_id='sandbox-read:stage5-verification'"
            ).fetchone()[0]
            worker_state = db.execute(
                "SELECT state FROM worker_processes WHERE intent_id=?", (intent.intent_id,)
            ).fetchone()[0]
        _assert(intent_status == "UNKNOWN", "uncertain Intent was not durably marked UNKNOWN")
        _assert(permission_state == "REVOKED", "revoked permission did not survive restart")
        _assert(worker_state == "ORPHAN_TERMINATED", "worker recovery status was not persisted")
        print("PASS: Intent, permission revocation and worker recovery status persisted")

        next_runtime.mark_clean_shutdown()
        next_runtime.close()
        reopened.close()

    if child is not None and child.poll() is None:
        child.terminate()
        child.wait(timeout=3)
    print("PASS: Stage 5 persistence and crash-recovery runtime verification completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
