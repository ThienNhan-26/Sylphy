import asyncio

import pytest

from sylphy_entity.contracts.execution import (
    ExecutionResult,
    ExecutionStatus,
    IntentOrigin,
    PermissionClass,
    ResourceMode,
    ResourceRequirement,
)
from sylphy_entity.execution.dispatcher import IntentDispatcher
from sylphy_entity.execution.permissions import PermissionPolicy
from sylphy_entity.execution.intent_router import IntentRouter
from sylphy_entity.execution.resource_arbiter import ResourceArbiter
from sylphy_entity.execution.worker_executor import SandboxWorkerExecutor
from sylphy_entity.runtime.process_manager import ProcessManager


@pytest.mark.asyncio
async def test_metadata_intent_executes_in_allowlisted_worker_with_evidence(sandbox, intent_factory):
    policy = PermissionPolicy(sandbox)
    process_manager = ProcessManager()
    handler = SandboxWorkerExecutor(process_manager)
    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    try:
        intent = intent_factory(scope=policy.scope)
        result = await dispatcher.execute(intent)
        assert result.status is ExecutionStatus.SUCCEEDED
        assert result.actual_result["relative_path"] == "notes.txt"
        assert result.actual_result["size_bytes"] == len("SYLPHY sandbox note".encode())
        assert any(item.startswith("sha256:") for item in result.evidence_ids)
        assert result.worker_pid is not None
        assert result.worker_return_code == 0
        assert process_manager.snapshots() == []
    finally:
        await process_manager.close()


@pytest.mark.asyncio
async def test_read_sandbox_text_is_bounded_and_returns_content_evidence(sandbox, intent_factory):
    policy = PermissionPolicy(sandbox, max_read_bytes=64)
    process_manager = ProcessManager()
    handler = SandboxWorkerExecutor(process_manager, read_limit_bytes=64)
    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"read_sandbox_text": handler},
    )
    try:
        intent = intent_factory(
            "notes.txt",
            capability="read_sandbox_text",
            permission=PermissionClass.READ_SANDBOX,
            scope=policy.scope,
        )
        result = await dispatcher.execute(intent)
        assert result.status is ExecutionStatus.SUCCEEDED
        assert result.actual_result["text"] == "SYLPHY sandbox note"
        import hashlib
        assert result.actual_result["content_sha256"] == hashlib.sha256(b"SYLPHY sandbox note").hexdigest()
        assert result.evidence_ids
        assert process_manager.snapshots() == []
    finally:
        await process_manager.close()


@pytest.mark.asyncio
async def test_unauthorized_scope_is_rejected_before_handler_call(sandbox, proposal_factory):
    policy = PermissionPolicy(sandbox)
    called = False

    async def handler(intent, target):
        nonlocal called
        called = True
        return ExecutionResult.immediate(
            intent.intent_id,
            ExecutionStatus.FAILED,
            error="must not execute",
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    from sylphy_entity.execution.intent_router import IntentRouter

    intent = IntentRouter().route(proposal_factory(scope="sandbox:/not/the/authorized/root"))
    result = await dispatcher.execute(intent)
    assert result.status is ExecutionStatus.REJECTED
    assert called is False


@pytest.mark.asyncio
async def test_worker_crash_is_reported_with_captured_log_evidence(sandbox, intent_factory):
    policy = PermissionPolicy(sandbox)
    process_manager = ProcessManager()
    worker = SandboxWorkerExecutor(process_manager)

    async def remove_before_worker(intent, target):
        target.path.unlink()
        return await worker(intent, target)

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": remove_before_worker},
    )
    try:
        result = await dispatcher.execute(intent_factory(scope=policy.scope))
        assert result.status is ExecutionStatus.FAILED
        assert result.worker_return_code != 0
        assert result.worker_pid is not None
        assert any(item.startswith("worker-log:sha256:") for item in result.evidence_ids)
        assert result.error and "failed" in result.error
    finally:
        await process_manager.close()


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_executes_handler_once(sandbox, proposal_factory):
    policy = PermissionPolicy(sandbox)
    called = 0
    gate = asyncio.Event()

    async def handler(intent, target):
        nonlocal called
        called += 1
        await gate.wait()
        return ExecutionResult.immediate(
            intent.intent_id,
            ExecutionStatus.SUCCEEDED,
            evidence_ids=("test-evidence",),
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    proposal = proposal_factory(scope=policy.scope)
    one = asyncio.create_task(dispatcher.execute(IntentRouter().route(proposal)))
    await asyncio.sleep(0)
    two = asyncio.create_task(dispatcher.execute(IntentRouter().route(proposal)))
    await asyncio.sleep(0.02)
    assert called == 1
    gate.set()
    first, second = await asyncio.gather(one, two)
    assert first.status is ExecutionStatus.SUCCEEDED
    assert second.status is ExecutionStatus.SUCCEEDED
    cached = await dispatcher.execute(IntentRouter().route(proposal))
    assert cached.deduplicated
    assert called == 1


@pytest.mark.asyncio
async def test_resource_conflict_blocks_execution_without_partial_claim(sandbox, proposal_factory):
    policy = PermissionPolicy(sandbox)
    arbiter = ResourceArbiter()
    from sylphy_entity.execution.intent_router import IntentRouter

    routed = IntentRouter().route(proposal_factory(scope=policy.scope))
    exclusive = await arbiter.acquire(
        "other-owner",
        (ResourceRequirement(routed.resources[0].resource_key, ResourceMode.EXCLUSIVE),),
    )
    called = False

    async def handler(intent, target):
        nonlocal called
        called = True
        return ExecutionResult.immediate(
            intent.intent_id,
            ExecutionStatus.SUCCEEDED,
            evidence_ids=("evidence",),
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=arbiter,
        handlers={"inspect_file_metadata": handler},
    )
    result = await dispatcher.execute(routed)
    assert result.status is ExecutionStatus.BLOCKED
    assert not called
    assert {item.owner_id for item in await arbiter.snapshot()} == {"other-owner"}
    await arbiter.release(exclusive)


@pytest.mark.asyncio
async def test_takeover_cancels_autonomous_intent_and_releases_resources(sandbox, proposal_factory):
    policy = PermissionPolicy(sandbox)
    arbiter = ResourceArbiter()
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def handler(intent, target):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaned.set()
            raise

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=arbiter,
        handlers={"inspect_file_metadata": handler},
    )
    from sylphy_entity.execution.intent_router import IntentRouter

    task = asyncio.create_task(dispatcher.execute(IntentRouter().route(proposal_factory(scope=policy.scope))))
    await started.wait()
    cancelled = await dispatcher.cancel_autonomous()
    result = await task
    assert cancelled == ("intent-test-1",)
    assert cleaned.is_set()
    assert result.status is ExecutionStatus.CANCELLED
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_user_origin_intent_is_not_cancelled_by_autonomy_takeover(sandbox, proposal_factory):
    policy = PermissionPolicy(sandbox)
    started = asyncio.Event()
    gate = asyncio.Event()

    async def handler(intent, target):
        started.set()
        await gate.wait()
        return ExecutionResult.immediate(
            intent.intent_id,
            ExecutionStatus.SUCCEEDED,
            evidence_ids=("user-action-evidence",),
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    from sylphy_entity.execution.intent_router import IntentRouter

    intent = IntentRouter().route(proposal_factory(scope=policy.scope))
    from dataclasses import replace

    intent = replace(intent, origin=IntentOrigin.USER)
    task = asyncio.create_task(dispatcher.execute(intent))
    await started.wait()
    assert await dispatcher.cancel_autonomous() == ()
    gate.set()
    result = await task
    assert result.status is ExecutionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_transient_resource_block_is_not_cached_and_same_intent_can_retry(sandbox, proposal_factory):
    policy = PermissionPolicy(sandbox)
    arbiter = ResourceArbiter()
    from sylphy_entity.execution.intent_router import IntentRouter

    intent = IntentRouter().route(proposal_factory(scope=policy.scope))
    hold = await arbiter.acquire(
        "temporary-owner",
        (ResourceRequirement(intent.resources[0].resource_key, ResourceMode.EXCLUSIVE),),
    )
    called = 0

    async def handler(current_intent, target):
        nonlocal called
        called += 1
        return ExecutionResult.immediate(
            current_intent.intent_id,
            ExecutionStatus.SUCCEEDED,
            evidence_ids=("verified-after-retry",),
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=arbiter,
        handlers={"inspect_file_metadata": handler},
    )
    blocked = await dispatcher.execute(intent)
    assert blocked.status is ExecutionStatus.BLOCKED
    await arbiter.release(hold)
    succeeded = await dispatcher.execute(intent)
    assert succeeded.status is ExecutionStatus.SUCCEEDED
    assert called == 1


@pytest.mark.asyncio
async def test_same_idempotency_key_cannot_change_origin_or_cancellation_policy(sandbox, proposal_factory):
    from dataclasses import replace
    from sylphy_entity.execution.intent_router import IntentRouter

    policy = PermissionPolicy(sandbox)
    intent = IntentRouter().route(proposal_factory(scope=policy.scope))
    called = 0

    async def handler(current_intent, target):
        nonlocal called
        called += 1
        return ExecutionResult.immediate(
            current_intent.intent_id,
            ExecutionStatus.SUCCEEDED,
            evidence_ids=("evidence",),
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    first = await dispatcher.execute(intent)
    changed = await dispatcher.execute(replace(intent, origin=IntentOrigin.USER))
    assert first.status is ExecutionStatus.SUCCEEDED
    assert changed.status is ExecutionStatus.REJECTED
    assert called == 1


def test_execution_result_actual_result_is_immutable():
    from datetime import datetime, timezone
    from sylphy_entity.contracts.execution import ExecutionResult

    payload = {"value": "initial"}
    result = ExecutionResult(
        intent_id="immutable-result",
        status=ExecutionStatus.SUCCEEDED,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        actual_result=payload,
        evidence_ids=("evidence",),
    )
    payload["value"] = "mutated"
    assert result.actual_result["value"] == "initial"
    with pytest.raises(TypeError):
        result.actual_result["value"] = "cannot-change"


@pytest.mark.asyncio
async def test_pause_and_resume_autonomy_cancels_running_work_and_blocks_new_intents(sandbox, proposal_factory):
    policy = PermissionPolicy(sandbox)
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def handler(intent, target):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaned.set()
            raise

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    first_proposal = proposal_factory(scope=policy.scope, intent_id="pause-running")
    first = asyncio.create_task(dispatcher.execute(IntentRouter().route(first_proposal)))
    await started.wait()
    cancelled = await dispatcher.pause_autonomy()
    first_result = await first
    assert cancelled == ("pause-running",)
    assert first_result.status is ExecutionStatus.CANCELLED
    assert cleaned.is_set()

    blocked_proposal = proposal_factory(scope=policy.scope, intent_id="pause-blocked")
    blocked = await dispatcher.execute(IntentRouter().route(blocked_proposal))
    assert blocked.status is ExecutionStatus.BLOCKED
    assert "paused" in (blocked.error or "")

    await dispatcher.resume_autonomy()
    ready = asyncio.Event()

    async def quick_handler(intent, target):
        ready.set()
        return ExecutionResult.immediate(
            intent.intent_id,
            ExecutionStatus.SUCCEEDED,
            evidence_ids=("after-resume",),
        )

    # Handler registry is intentionally fixed; the resumed request can use the
    # same registered handler only after the test swaps the capability implementation.
    dispatcher._handlers["inspect_file_metadata"] = quick_handler
    resumed = await dispatcher.execute(
        IntentRouter().route(proposal_factory(scope=policy.scope, intent_id="after-resume"))
    )
    assert ready.is_set()
    assert resumed.status is ExecutionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_scope_revocation_cancels_active_work_and_denies_future_execution(sandbox, proposal_factory):
    policy = PermissionPolicy(sandbox)
    started = asyncio.Event()
    cleaned = asyncio.Event()
    calls = 0

    async def handler(intent, target):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaned.set()
                raise
        return ExecutionResult.immediate(
            intent.intent_id,
            ExecutionStatus.SUCCEEDED,
            evidence_ids=("permission-restored",),
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    intent = IntentRouter().route(proposal_factory(scope=policy.scope, intent_id="revoke-active"))
    task = asyncio.create_task(dispatcher.execute(intent))
    await started.wait()
    cancelled = await dispatcher.revoke_scope(policy.scope)
    result = await task
    assert cancelled == (intent.intent_id,)
    assert cleaned.is_set()
    assert result.status is ExecutionStatus.CANCELLED

    later = IntentRouter().route(proposal_factory(scope=policy.scope, intent_id="after-revoke"))
    rejected = await dispatcher.execute(later)
    assert rejected.status is ExecutionStatus.REJECTED
    assert "revoked" in (rejected.error or "")

    await dispatcher.restore_scope(policy.scope)
    restored = await dispatcher.execute(later)
    assert restored.status is ExecutionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_user_origin_intent_preempts_autonomous_work_before_dispatch(sandbox, proposal_factory):
    policy = PermissionPolicy(sandbox)
    autonomous_started = asyncio.Event()
    autonomous_cancelled = asyncio.Event()

    async def handler(intent, target):
        if intent.origin is IntentOrigin.AUTONOMOUS:
            autonomous_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                autonomous_cancelled.set()
                raise
        return ExecutionResult.immediate(
            intent.intent_id,
            ExecutionStatus.SUCCEEDED,
            evidence_ids=("user-priority-evidence",),
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    automatic = asyncio.create_task(
        dispatcher.execute(IntentRouter().route(proposal_factory(scope=policy.scope, intent_id="autonomous-first")))
    )
    await autonomous_started.wait()
    user_intent = IntentRouter().route(
        proposal_factory(scope=policy.scope, intent_id="user-priority")
    )
    from dataclasses import replace

    user_intent = replace(user_intent, origin=IntentOrigin.USER)
    user_result = await dispatcher.execute(user_intent)
    automatic_result = await automatic
    assert autonomous_cancelled.is_set()
    assert automatic_result.status is ExecutionStatus.CANCELLED
    assert user_result.status is ExecutionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_user_takeover_drains_atomic_work_and_blocks_new_autonomous_admission(sandbox, proposal_factory):
    from dataclasses import replace
    from sylphy_entity.contracts.execution import CancellationPolicy

    policy = PermissionPolicy(sandbox)
    atomic_started = asyncio.Event()
    allow_atomic_finish = asyncio.Event()
    user_started = asyncio.Event()
    allow_user_finish = asyncio.Event()
    late_auto_started = asyncio.Event()

    async def handler(intent, target):
        if intent.intent_id == "atomic-auto":
            atomic_started.set()
            await allow_atomic_finish.wait()
            return ExecutionResult.immediate(
                intent.intent_id, ExecutionStatus.SUCCEEDED, evidence_ids=("atomic-evidence",)
            )
        if intent.intent_id == "user-takeover":
            user_started.set()
            await allow_user_finish.wait()
            return ExecutionResult.immediate(
                intent.intent_id, ExecutionStatus.SUCCEEDED, evidence_ids=("user-evidence",)
            )
        late_auto_started.set()
        return ExecutionResult.immediate(
            intent.intent_id, ExecutionStatus.SUCCEEDED, evidence_ids=("late-auto-evidence",)
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    router = IntentRouter()
    atomic = replace(
        router.route(proposal_factory(scope=policy.scope, intent_id="atomic-auto")),
        cancellation_policy=CancellationPolicy.FINISH_ATOMIC,
    )
    automatic_task = asyncio.create_task(dispatcher.execute(atomic))
    await atomic_started.wait()

    user = replace(
        router.route(proposal_factory(scope=policy.scope, intent_id="user-takeover")),
        origin=IntentOrigin.USER,
    )
    user_task = asyncio.create_task(dispatcher.execute(user))
    await asyncio.sleep(0.02)
    assert not user_started.is_set(), "user request must wait for FINISH_ATOMIC work"

    late_auto = router.route(proposal_factory(scope=policy.scope, intent_id="late-auto"))
    late_auto_task = asyncio.create_task(dispatcher.execute(late_auto))
    await asyncio.sleep(0.02)
    assert not late_auto_started.is_set(), "autonomous work must not pass the user takeover gate"

    allow_atomic_finish.set()
    await user_started.wait()
    late_result = await late_auto_task
    assert late_result.status is ExecutionStatus.BLOCKED
    assert "user-origin execution has priority" in (late_result.error or "")
    assert not late_auto_started.is_set()

    allow_user_finish.set()
    atomic_result, user_result = await asyncio.gather(automatic_task, user_task)
    assert atomic_result.status is ExecutionStatus.SUCCEEDED
    assert user_result.status is ExecutionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_idempotency_fingerprint_includes_reason_and_priority(sandbox, proposal_factory):
    from dataclasses import replace
    from sylphy_entity.execution.intent_router import IntentRouter

    policy = PermissionPolicy(sandbox)
    intent = IntentRouter().route(proposal_factory(scope=policy.scope))
    calls = 0

    async def handler(current_intent, target):
        nonlocal calls
        calls += 1
        return ExecutionResult.immediate(
            current_intent.intent_id, ExecutionStatus.SUCCEEDED, evidence_ids=("intent-evidence",)
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    original = await dispatcher.execute(intent)
    modified = await dispatcher.execute(replace(intent, reason="changed reason"))
    changed_priority = await dispatcher.execute(replace(intent, priority=0.9))

    assert original.status is ExecutionStatus.SUCCEEDED
    assert modified.status is ExecutionStatus.REJECTED
    assert changed_priority.status is ExecutionStatus.REJECTED
    assert calls == 1


@pytest.mark.asyncio
async def test_expired_intent_deadline_is_blocked_before_handler_invocation(sandbox, proposal_factory):
    import time
    from dataclasses import replace
    from sylphy_entity.execution.intent_router import IntentRouter

    policy = PermissionPolicy(sandbox)
    intent = IntentRouter().route(proposal_factory(scope=policy.scope))
    now = time.monotonic()
    expired = replace(
        intent,
        created_at_monotonic=now - 5.0,
        deadline_at_monotonic=now - 0.1,
    )
    called = False

    async def handler(current_intent, target):
        nonlocal called
        called = True
        return ExecutionResult.immediate(
            current_intent.intent_id, ExecutionStatus.SUCCEEDED, evidence_ids=("should-not-run",)
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    result = await dispatcher.execute(expired)
    assert result.status is ExecutionStatus.BLOCKED
    assert "deadline expired" in (result.error or "")
    assert not called


@pytest.mark.asyncio
async def test_handler_timeout_returns_failure_and_releases_resource_lease(sandbox, proposal_factory):
    from dataclasses import replace
    from sylphy_entity.execution.intent_router import IntentRouter

    policy = PermissionPolicy(sandbox)
    base = IntentRouter().route(proposal_factory(scope=policy.scope))
    intent = replace(base, timeout_seconds=0.05)
    arbiter = ResourceArbiter()

    async def slow_handler(current_intent, target):
        await asyncio.sleep(1.0)
        return ExecutionResult.immediate(
            current_intent.intent_id, ExecutionStatus.SUCCEEDED, evidence_ids=("too-late",)
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=arbiter,
        handlers={"inspect_file_metadata": slow_handler},
    )
    result = await dispatcher.execute(intent)
    assert result.status is ExecutionStatus.FAILED
    assert "timed out" in (result.error or "")
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_pause_autonomy_waits_for_non_cancellable_atomic_boundary(sandbox, proposal_factory):
    from dataclasses import replace
    from sylphy_entity.contracts.execution import CancellationPolicy

    policy = PermissionPolicy(sandbox)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def handler(intent, target):
        started.set()
        await finish.wait()
        return ExecutionResult.immediate(
            intent.intent_id, ExecutionStatus.SUCCEEDED, evidence_ids=("atomic-finish",)
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    intent = replace(
        IntentRouter().route(proposal_factory(scope=policy.scope, intent_id="atomic-pause")),
        cancellation_policy=CancellationPolicy.FINISH_ATOMIC,
    )
    running = asyncio.create_task(dispatcher.execute(intent))
    await started.wait()
    pause_task = asyncio.create_task(dispatcher.pause_autonomy())
    await asyncio.sleep(0.02)
    assert not pause_task.done(), "pause must wait for the atomic boundary"
    finish.set()
    paused_ids = await pause_task
    result = await running
    assert paused_ids == ("atomic-pause",)
    assert result.status is ExecutionStatus.SUCCEEDED
    assert await dispatcher.active_intent_ids() == ()


@pytest.mark.asyncio
async def test_scope_revocation_waits_for_atomic_work_but_blocks_new_work(sandbox, proposal_factory):
    from dataclasses import replace
    from sylphy_entity.contracts.execution import CancellationPolicy

    policy = PermissionPolicy(sandbox)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def handler(intent, target):
        started.set()
        await finish.wait()
        return ExecutionResult.immediate(
            intent.intent_id, ExecutionStatus.SUCCEEDED, evidence_ids=("atomic-scope-result",)
        )

    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": handler},
    )
    intent = replace(
        IntentRouter().route(proposal_factory(scope=policy.scope, intent_id="atomic-revoke")),
        cancellation_policy=CancellationPolicy.FINISH_ATOMIC,
    )
    running = asyncio.create_task(dispatcher.execute(intent))
    await started.wait()
    revoke_task = asyncio.create_task(dispatcher.revoke_scope(policy.scope))
    await asyncio.sleep(0.02)
    assert not revoke_task.done(), "scope revocation must wait for the declared atomic boundary"

    new_intent = IntentRouter().route(proposal_factory(scope=policy.scope, intent_id="after-atomic-revoke"))
    blocked_or_rejected = await dispatcher.execute(new_intent)
    assert blocked_or_rejected.status is ExecutionStatus.REJECTED

    finish.set()
    affected = await revoke_task
    result = await running
    assert affected == ("atomic-revoke",)
    assert result.status is ExecutionStatus.SUCCEEDED
