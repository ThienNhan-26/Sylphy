from dataclasses import dataclass
import time
from pathlib import Path

from sylphy_entity.contracts.execution import (
    CancellationPolicy, Intent, IntentOrigin, PermissionClass,
    ResourceLease, ResourceMode, ResourceRequirement,
)
from sylphy_entity.persistence.event_store import EventStore
from sylphy_entity.persistence.recovery import RecoveryManager
from sylphy_entity.persistence.state_repository import StateRepository


def _intent(intent_id: str) -> Intent:
    now = time.monotonic()
    return Intent(
        intent_id=intent_id, goal_id=f"goal-{intent_id}", capability="inspect_file_metadata",
        target="note.txt", arguments=(("relative_path", "note.txt"),), reason="recovery test",
        preconditions=("target_exists",), permission_class=PermissionClass.OBSERVE_SANDBOX,
        permission_scope="sandbox:/allowed",
        resources=(ResourceRequirement("filesystem:sandbox:/allowed:note.txt"),),
        priority=0.5, expected_outcome="metadata evidence", idempotency_key=f"key-{intent_id}",
        created_at_monotonic=now, deadline_at_monotonic=None, timeout_seconds=5.0,
        cancellation_policy=CancellationPolicy.CANCEL_ON_TAKEOVER, origin=IntentOrigin.AUTONOMOUS,
    )


def test_recovery_classifies_incomplete_intents_and_never_kills_unverified_pid(entity_db):
    store = EventStore(entity_db)
    accepted = _intent("accepted")
    dispatching = _intent("dispatching")
    store.record_intent(accepted)
    store.record_intent(dispatching)
    store.mark_intent_dispatching(dispatching)
    store.record_worker_started(
        intent_id="dispatching", pid=1234, create_time=100.0,
        daemon_instance_id="old-daemon", worker_name="verified-worker",
    )
    store.record_worker_started(
        intent_id="dispatching", pid=5678, create_time=200.0,
        daemon_instance_id="old-daemon", worker_name="pid-reused-worker",
    )
    lease = ResourceLease(
        lease_id="lease-1", owner_id="dispatching", resource_key="filesystem:/allowed:note.txt",
        mode=ResourceMode.SHARED, acquired_at_monotonic=10.0, expires_at_monotonic=20.0,
    )
    store.record_leases("dispatching", (lease,))
    terminated: list[tuple[int, float]] = []

    manager = RecoveryManager(
        entity_db, store,
        process_matches=lambda pid, created: pid == 1234 and created == 100.0,
        terminate_verified_process=lambda pid, created: terminated.append((pid, created)) or True,
    )
    report = manager.recover(previous_shutdown_clean=False)

    assert report.interrupted_intent_ids == ("accepted",)
    assert report.unknown_intent_ids == ("dispatching",)
    assert report.terminated_worker_ids == ("dispatching:1234:100.0",)
    assert report.missing_worker_ids == ("dispatching:5678:200.0",)
    assert terminated == [(1234, 100.0)]
    assert report.abandoned_lease_count == 1
    with entity_db.read() as db:
        statuses = dict(db.execute("SELECT intent_id,status FROM intents").fetchall())
        execution = db.execute("SELECT status FROM executions WHERE intent_id='dispatching'").fetchone()
        lease_state = db.execute("SELECT state FROM resource_leases WHERE lease_id='lease-1'").fetchone()
    assert statuses == {"accepted": "INTERRUPTED", "dispatching": "UNKNOWN"}
    assert execution[0] == "UNKNOWN"
    assert lease_state[0] == "ABANDONED"


def test_worker_without_creation_time_is_not_terminated(entity_db):
    store = EventStore(entity_db)
    intent = _intent("no-create-time")
    store.record_intent(intent)
    store.mark_intent_dispatching(intent)
    store.record_worker_started(
        intent_id=intent.intent_id, pid=4321, create_time=None,
        daemon_instance_id="old-daemon", worker_name="unknown-worker",
    )
    called = False

    def terminate(pid, created):
        nonlocal called
        called = True
        return True

    report = RecoveryManager(
        entity_db, store, process_matches=lambda pid, created: True,
        terminate_verified_process=terminate,
    ).recover(previous_shutdown_clean=False)
    assert report.unverified_worker_ids == (f"{intent.intent_id}:4321:unknown",)
    assert called is False


def test_recovery_terminates_a_real_worker_only_when_pid_and_creation_time_match(entity_db):
    import subprocess
    import sys

    import psutil

    store = EventStore(entity_db)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        created = psutil.Process(child.pid).create_time()
        store.record_worker_started(
            intent_id="real-orphan", pid=child.pid, create_time=created,
            daemon_instance_id="previous-daemon", worker_name="real-worker",
        )
        report = RecoveryManager(entity_db, store).recover(previous_shutdown_clean=False)
        assert report.terminated_worker_ids == (f"real-orphan:{child.pid}:{created}",)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)


def test_recovery_does_not_kill_real_process_when_creation_time_does_not_match(entity_db):
    import subprocess
    import sys

    import psutil

    store = EventStore(entity_db)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        created = psutil.Process(child.pid).create_time()
        wrong_created = created - 100.0
        store.record_worker_started(
            intent_id="reused-pid", pid=child.pid, create_time=wrong_created,
            daemon_instance_id="previous-daemon", worker_name="wrong-identity",
        )
        report = RecoveryManager(entity_db, store).recover(previous_shutdown_clean=False)
        assert report.missing_worker_ids == (f"reused-pid:{child.pid}:{wrong_created}",)
        assert child.poll() is None
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)
