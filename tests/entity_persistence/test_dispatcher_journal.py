import asyncio
from datetime import datetime, timezone
import hashlib
import time

import pytest

from sylphy_entity.contracts.execution import (
    CancellationPolicy, ExecutionResult, ExecutionStatus, Intent, IntentOrigin,
    PermissionClass, ResourceMode, ResourceRequirement,
)
from sylphy_entity.execution.dispatcher import IntentDispatcher
from sylphy_entity.execution.permissions import PermissionDenied, PermissionPolicy
from sylphy_entity.execution.resource_arbiter import ResourceArbiter
from sylphy_entity.execution.worker_executor import SandboxWorkerExecutor
from sylphy_entity.persistence.event_store import EventStore
from sylphy_entity.persistence.state_repository import StateRepository
from sylphy_entity.runtime.process_manager import ProcessManager


def _intent(scope: str, *, target="note.txt", intent_id="persisted-intent"):
    now = time.monotonic()
    return Intent(
        intent_id=intent_id, goal_id="persisted-goal", capability="inspect_file_metadata",
        target=target, arguments=(("relative_path", target),), reason="persist before side effect",
        preconditions=("target_exists",), permission_class=PermissionClass.OBSERVE_SANDBOX,
        permission_scope=scope,
        resources=(ResourceRequirement(f"filesystem:{scope}:{target}", ResourceMode.SHARED),),
        priority=0.7, expected_outcome="verified metadata", idempotency_key=f"key-{intent_id}",
        created_at_monotonic=now, deadline_at_monotonic=None, timeout_seconds=5.0,
        cancellation_policy=CancellationPolicy.CANCEL_ON_TAKEOVER, origin=IntentOrigin.AUTONOMOUS,
    )


@pytest.mark.asyncio
async def test_dispatcher_journals_intent_before_handler_and_persists_result(entity_db, tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "note.txt").write_text("persistent evidence", encoding="utf-8")
    store = EventStore(entity_db)
    policy = PermissionPolicy(sandbox)
    observed_status: list[str] = []

    async def handler(intent, target):
        with entity_db.read() as db:
            row = db.execute("SELECT status FROM intents WHERE intent_id=?", (intent.intent_id,)).fetchone()
        observed_status.append(row[0])
        digest = hashlib.sha256(target.path.read_bytes()).hexdigest()
        return ExecutionResult.immediate(
            intent.intent_id, ExecutionStatus.SUCCEEDED,
            actual_result={"content_sha256": digest}, evidence_ids=(f"sha256:{digest}",),
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy, resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler}, journal=store,
    )
    result = await dispatcher.execute(_intent(policy.scope))
    assert result.status is ExecutionStatus.SUCCEEDED
    assert observed_status == ["DISPATCHING"]
    with entity_db.read() as db:
        intent_row = db.execute("SELECT status FROM intents WHERE intent_id='persisted-intent'").fetchone()
        execution = db.execute(
            "SELECT status,evidence_ids_json FROM executions WHERE intent_id='persisted-intent'"
        ).fetchone()
        evidence = db.execute("SELECT sha256 FROM evidence WHERE intent_id='persisted-intent'").fetchone()
        lease_states = [row[0] for row in db.execute("SELECT state FROM resource_leases")]
    assert intent_row[0] == "SUCCEEDED"
    assert execution[0] == "SUCCEEDED"
    assert result.evidence_ids[0] in execution[1]
    assert evidence[0] == result.actual_result["content_sha256"]
    assert lease_states == ["RELEASED"]


@pytest.mark.asyncio
async def test_worker_identity_is_persisted_before_worker_result(entity_db, tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "note.txt").write_text("hello worker", encoding="utf-8")
    store = EventStore(entity_db)
    policy = PermissionPolicy(sandbox)
    process_manager = ProcessManager()
    recorded = []

    def on_started(intent, worker):
        recorded.append(worker.pid)
        store.record_worker_started(
            intent_id=intent.intent_id, pid=worker.pid, create_time=worker.create_time,
            daemon_instance_id="daemon-test", worker_name=worker.name,
        )

    worker = SandboxWorkerExecutor(process_manager, on_worker_started=on_started)
    dispatcher = IntentDispatcher(
        permission_policy=policy, resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": worker}, journal=store,
    )
    try:
        result = await dispatcher.execute(_intent(policy.scope, intent_id="worker-persisted"))
    finally:
        await process_manager.close()
    assert result.status is ExecutionStatus.SUCCEEDED
    assert recorded == [result.worker_pid]
    with entity_db.read() as db:
        row = db.execute(
            "SELECT pid,create_time,state FROM worker_processes WHERE intent_id='worker-persisted'"
        ).fetchone()
    assert row[0] == result.worker_pid
    assert row[1] == result.worker_create_time
    assert row[2] == "EXITED"


def test_permission_revocation_survives_policy_recreation(entity_db, tmp_path):
    repo = StateRepository(entity_db)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    first = PermissionPolicy(sandbox, state_store=repo)
    scope = first.scope
    first.revoke_scope(scope)
    second = PermissionPolicy(sandbox, state_store=repo)
    assert second.is_revoked(scope)
    second.restore_scope(scope)
    third = PermissionPolicy(sandbox, state_store=repo)
    assert not third.is_revoked(scope)

@pytest.mark.asyncio
async def test_completed_idempotency_result_survives_new_dispatcher_instance(entity_db, tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "note.txt").write_text("once only", encoding="utf-8")
    store = EventStore(entity_db)
    policy = PermissionPolicy(sandbox)
    calls = 0

    async def handler(intent, target):
        nonlocal calls
        calls += 1
        digest = hashlib.sha256(target.path.read_bytes()).hexdigest()
        return ExecutionResult.immediate(
            intent.intent_id, ExecutionStatus.SUCCEEDED,
            actual_result={"content_sha256": digest}, evidence_ids=(f"sha256:{digest}",),
        )

    intent = _intent(policy.scope, intent_id="cross-restart-idempotency")
    first = IntentDispatcher(
        permission_policy=policy, resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler}, journal=store,
    )
    result1 = await first.execute(intent)
    assert result1.status is ExecutionStatus.SUCCEEDED

    # Reopen the actual SQLite database to prove the result survives a process
    # restart, not merely a new in-memory Dispatcher over the same connection.
    entity_db.close()
    entity_db.open()
    reopened_store = EventStore(entity_db)
    second = IntentDispatcher(
        permission_policy=policy, resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler}, journal=reopened_store,
    )
    result2 = await second.execute(intent)
    assert result2.status is ExecutionStatus.SUCCEEDED
    assert result2.deduplicated is True
    assert result2.evidence_ids == result1.evidence_ids
    assert calls == 1


@pytest.mark.asyncio
async def test_unresolved_persisted_intent_is_not_redispatched(entity_db, tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "note.txt").write_text("maybe executed", encoding="utf-8")
    store = EventStore(entity_db)
    policy = PermissionPolicy(sandbox)
    intent = _intent(policy.scope, intent_id="unresolved-idempotency")
    store.record_intent(intent)
    store.mark_intent_dispatching(intent)
    calls = 0

    async def handler(intent, target):
        nonlocal calls
        calls += 1
        return ExecutionResult.immediate(
            intent.intent_id, ExecutionStatus.SUCCEEDED, evidence_ids=("should-not-run",)
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy, resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler}, journal=store,
    )
    result = await dispatcher.execute(intent)
    assert result.status is ExecutionStatus.UNKNOWN
    assert calls == 0
