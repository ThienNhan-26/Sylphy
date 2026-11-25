"""Authorize, arbitrate, dispatch and verify one bounded Intent."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Awaitable, Callable, Mapping, Protocol

from sylphy_entity.contracts.execution import (
    CancellationPolicy,
    ExecutionResult,
    ExecutionStatus,
    Intent,
    IntentOrigin,
)
from .permissions import AuthorizedTarget, PermissionDenied, PermissionPolicy
from .resource_arbiter import ResourceArbiter, ResourceBusy


CapabilityHandler = Callable[[Intent, AuthorizedTarget], Awaitable[ExecutionResult]]


class ExecutionJournal(Protocol):
    """Optional durable journal. Dispatch cannot cross the side-effect boundary without it."""

    def lookup_intent(self, intent: Intent): ...
    def record_intent(self, intent: Intent) -> None: ...
    def mark_intent_dispatching(self, intent: Intent) -> str: ...
    def record_leases(self, intent_id: str, leases) -> None: ...
    def release_leases(self, leases) -> None: ...
    def finish_execution(self, intent: Intent, result: ExecutionResult) -> None: ...


class IntentDispatcher:
    """The sole Stage-4 gateway from an Intent to a registered capability."""

    def __init__(
        self,
        *,
        permission_policy: PermissionPolicy,
        resource_arbiter: ResourceArbiter,
        handlers: Mapping[str, CapabilityHandler],
        max_cached_results: int = 1024,
        journal: ExecutionJournal | None = None,
    ) -> None:
        if max_cached_results < 1:
            raise ValueError("max_cached_results must be positive")
        self._permission_policy = permission_policy
        self._arbiter = resource_arbiter
        self._handlers = dict(handlers)
        self._journal = journal
        self._max_cached_results = max_cached_results
        self._lock = asyncio.Lock()
        # Serializes admission so a user takeover cannot be bypassed by a new
        # autonomous Intent arriving between cancellation and user dispatch.
        self._admission_lock = asyncio.Lock()
        self._user_inflight_keys: set[str] = set()
        self._inflight: dict[str, asyncio.Task[ExecutionResult]] = {}
        self._inflight_fingerprints: dict[str, tuple[object, ...]] = {}
        self._inflight_intents: dict[str, Intent] = {}
        self._completed: OrderedDict[str, tuple[tuple[object, ...], ExecutionResult]] = OrderedDict()
        self._autonomy_paused = False

    async def execute(self, intent: Intent) -> ExecutionResult:
        key = intent.idempotency_key
        fingerprint = self._fingerprint(intent)
        async with self._admission_lock:
            if intent.origin is IntentOrigin.USER:
                # Complete takeover before admitting the user request. New
                # autonomous work is held at the admission gate during this
                # handoff and while any user-origin Intent is active.
                await self._prepare_user_takeover()

            async with self._lock:
                if intent.origin is IntentOrigin.AUTONOMOUS and self._autonomy_paused:
                    return ExecutionResult.immediate(
                        intent.intent_id,
                        ExecutionStatus.BLOCKED,
                        error="autonomous execution is paused by the user",
                    )
                if intent.origin is IntentOrigin.AUTONOMOUS and self._user_inflight_keys:
                    return ExecutionResult.immediate(
                        intent.intent_id,
                        ExecutionStatus.BLOCKED,
                        error="user-origin execution has priority",
                    )

                cached = self._completed.get(key)
                if cached is not None:
                    prior_fingerprint, result = cached
                    self._completed.move_to_end(key)
                    if prior_fingerprint != fingerprint:
                        return ExecutionResult.immediate(
                            intent.intent_id,
                            ExecutionStatus.REJECTED,
                            error="idempotency key reused with a different Intent",
                        )
                    return self._as_deduplicated(result)

                running = self._inflight.get(key)
                if running is not None:
                    if self._inflight_fingerprints[key] != fingerprint:
                        return ExecutionResult.immediate(
                            intent.intent_id,
                            ExecutionStatus.REJECTED,
                            error="idempotency key is already active for a different Intent",
                        )
                    task = running
                else:
                    # Persisted idempotency survives process restart. A completed
                    # side effect returns its recorded result; an uncertain one is
                    # blocked until recovery/re-observation establishes its outcome.
                    if self._journal is not None:
                        persisted = self._journal.lookup_intent(intent)
                        if persisted is not None:
                            if persisted.result is not None:
                                return persisted.result
                            return ExecutionResult.immediate(
                                intent.intent_id, ExecutionStatus.UNKNOWN,
                                error=(
                                    "persisted Intent has unresolved state "
                                    f"{persisted.status}; reconcile before retry"
                                ),
                            )
                        self._journal.record_intent(intent)
                    if intent.origin is IntentOrigin.USER:
                        self._user_inflight_keys.add(key)
                    task = asyncio.create_task(
                        self._execute_and_record(intent, fingerprint),
                        name=f"sylphy-intent:{intent.intent_id}",
                    )
                    self._inflight[key] = task
                    self._inflight_fingerprints[key] = fingerprint
                    self._inflight_intents[key] = intent
        # Cancelling an API/CLI waiter must not cancel an action. Explicit
        # takeover and shutdown use the dispatcher-owned task.
        return await asyncio.shield(task)

    async def _prepare_user_takeover(self) -> tuple[str, ...]:
        """Cancel interruptible autonomous work and drain atomic work first."""
        async with self._lock:
            cancellable: list[tuple[str, asyncio.Task[ExecutionResult]]] = []
            atomic: list[tuple[str, asyncio.Task[ExecutionResult]]] = []
            for key, task in self._inflight.items():
                intent = self._inflight_intents.get(key)
                if intent is None or intent.origin is not IntentOrigin.AUTONOMOUS:
                    continue
                entry = (intent.intent_id, task)
                if intent.cancellation_policy is CancellationPolicy.CANCEL_ON_TAKEOVER:
                    cancellable.append(entry)
                else:
                    atomic.append(entry)
        for _, task in cancellable:
            task.cancel()
        if cancellable:
            await asyncio.gather(
                *(asyncio.shield(task) for _, task in cancellable),
                return_exceptions=True,
            )
        if atomic:
            # FINISH_ATOMIC means exactly that: let the bounded operation
            # finish, but do not admit a later autonomous task ahead of user.
            await asyncio.gather(
                *(asyncio.shield(task) for _, task in atomic),
                return_exceptions=True,
            )
        return tuple(intent_id for intent_id, _ in cancellable + atomic)

    async def cancel_autonomous(self) -> tuple[str, ...]:
        async with self._lock:
            selected = list(self._inflight.items())
            cancel = []
            for key, task in selected:
                intent = self._inflight_intents.get(key)
                if intent is not None and intent.origin is IntentOrigin.AUTONOMOUS:
                    if intent.cancellation_policy is CancellationPolicy.CANCEL_ON_TAKEOVER:
                        cancel.append((key, task, intent.intent_id))
        for _, task, _ in cancel:
            task.cancel()
        if cancel:
            await asyncio.gather(*(asyncio.shield(task) for _, task, _ in cancel), return_exceptions=True)
        return tuple(intent_id for _, _, intent_id in cancel)

    async def pause_autonomy(self) -> tuple[str, ...]:
        async with self._lock:
            self._autonomy_paused = True
        # Interruptible work is cancelled; bounded atomic work is drained
        # before pause is reported complete.
        return await self._prepare_user_takeover()

    async def resume_autonomy(self) -> None:
        async with self._lock:
            self._autonomy_paused = False

    async def revoke_scope(self, scope: str) -> tuple[str, ...]:
        # Revoke first so no new request can pass authorization while active
        # work is stopped or allowed to finish its explicitly atomic boundary.
        self._permission_policy.revoke_scope(scope)
        async with self._lock:
            cancellable: list[tuple[str, asyncio.Task[ExecutionResult]]] = []
            atomic: list[tuple[str, asyncio.Task[ExecutionResult]]] = []
            for key, task in self._inflight.items():
                intent = self._inflight_intents.get(key)
                if intent is None or intent.permission_scope != scope:
                    continue
                entry = (intent.intent_id, task)
                if intent.cancellation_policy is CancellationPolicy.CANCEL_ON_TAKEOVER:
                    cancellable.append(entry)
                else:
                    atomic.append(entry)
        for _, task in cancellable:
            task.cancel()
        if cancellable:
            await asyncio.gather(
                *(asyncio.shield(task) for _, task in cancellable),
                return_exceptions=True,
            )
        if atomic:
            await asyncio.gather(
                *(asyncio.shield(task) for _, task in atomic),
                return_exceptions=True,
            )
        return tuple(intent_id for intent_id, _ in cancellable + atomic)

    async def restore_scope(self, scope: str) -> None:
        self._permission_policy.restore_scope(scope)

    async def active_intent_ids(self) -> tuple[str, ...]:
        async with self._lock:
            result: list[str] = []
            for intent in self._inflight_intents.values():
                result.append(intent.intent_id)
            return tuple(sorted(result))

    async def _execute_and_record(
        self,
        intent: Intent,
        fingerprint: tuple[object, ...],
    ) -> ExecutionResult:
        try:
            result = await self._execute_once(intent)
        except asyncio.CancelledError:
            result = ExecutionResult.immediate(
                intent.intent_id,
                ExecutionStatus.CANCELLED,
                error="Intent execution cancelled by Runtime control",
            )
        except Exception as exc:  # Defensive containment at the execution boundary.
            result = ExecutionResult.immediate(
                intent.intent_id,
                ExecutionStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
        if self._journal is not None:
            try:
                self._journal.finish_execution(intent, result)
            except Exception as exc:
                # The side effect may already have happened. If the result cannot
                # be durably committed, never report it as a verified success.
                result = ExecutionResult.immediate(
                    intent.intent_id,
                    ExecutionStatus.UNKNOWN,
                    error=f"execution result could not be durably recorded: {type(exc).__name__}: {exc}",
                    actual_result={"prior_status": result.status.value},
                )
        async with self._lock:
            self._inflight.pop(intent.idempotency_key, None)
            self._inflight_fingerprints.pop(intent.idempotency_key, None)
            self._inflight_intents.pop(intent.idempotency_key, None)
            self._user_inflight_keys.discard(intent.idempotency_key)
            if result.status in {ExecutionStatus.BLOCKED, ExecutionStatus.REJECTED}:
                # Contention and permission denials can be transient (e.g. a
                # resource is released or a user restores a revoked scope).
                # They must not poison an idempotency key permanently.
                self._completed.pop(intent.idempotency_key, None)
            else:
                self._completed[intent.idempotency_key] = (fingerprint, result)
                self._completed.move_to_end(intent.idempotency_key)
            while len(self._completed) > self._max_cached_results:
                self._completed.popitem(last=False)
        return result

    async def _execute_once(self, intent: Intent) -> ExecutionResult:
        started = datetime.now(timezone.utc)
        leases = ()
        try:
            import time

            if intent.deadline_at_monotonic is not None and time.monotonic() >= intent.deadline_at_monotonic:
                return ExecutionResult.immediate(
                    intent.intent_id,
                    ExecutionStatus.BLOCKED,
                    error="Intent deadline expired before dispatch",
                )
            handler = self._handlers.get(intent.capability)
            if handler is None:
                return ExecutionResult.immediate(
                    intent.intent_id,
                    ExecutionStatus.REJECTED,
                    error=f"no executor registered for capability: {intent.capability}",
                )
            target = self._permission_policy.authorize(intent)
            leases = await self._arbiter.acquire(
                intent.intent_id,
                intent.resources,
                ttl_seconds=intent.timeout_seconds + 5.0,
            )
            if self._journal is not None:
                self._journal.record_leases(intent.intent_id, leases)
                # This durable transition is committed before invoking a handler
                # which may spawn a worker or cause an external side effect.
                self._journal.mark_intent_dispatching(intent)
            result = await asyncio.wait_for(handler(intent, target), timeout=intent.timeout_seconds)
            if result.intent_id != intent.intent_id:
                return ExecutionResult.immediate(
                    intent.intent_id,
                    ExecutionStatus.FAILED,
                    error="executor returned a result for a different Intent",
                )
            if result.status is ExecutionStatus.SUCCEEDED and not result.evidence_ids:
                return ExecutionResult.immediate(
                    intent.intent_id,
                    ExecutionStatus.FAILED,
                    error="executor claimed success without evidence",
                )
            return result
        except PermissionDenied as exc:
            return ExecutionResult.immediate(intent.intent_id, ExecutionStatus.REJECTED, error=str(exc))
        except ResourceBusy as exc:
            return ExecutionResult.immediate(intent.intent_id, ExecutionStatus.BLOCKED, error=str(exc))
        except asyncio.TimeoutError:
            return ExecutionResult.immediate(
                intent.intent_id,
                ExecutionStatus.FAILED,
                error=f"capability timed out after {intent.timeout_seconds:.1f}s",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ExecutionResult.immediate(
                intent.intent_id,
                ExecutionStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if leases:
                await self._arbiter.release(leases)
                if self._journal is not None:
                    self._journal.release_leases(leases)

    @staticmethod
    def _fingerprint(intent: Intent) -> tuple[object, ...]:
        return (
            intent.intent_id,
            intent.goal_id,
            intent.capability,
            intent.target,
            intent.arguments,
            intent.permission_class,
            intent.permission_scope,
            intent.resources,
            intent.expected_outcome,
            intent.preconditions,
            intent.reason,
            intent.priority,
            intent.origin,
            intent.source_observation_id,
            intent.cancellation_policy,
            intent.timeout_seconds,
            round(intent.deadline_at_monotonic - intent.created_at_monotonic, 6)
            if intent.deadline_at_monotonic is not None else None,
        )

    @staticmethod
    def _as_deduplicated(result: ExecutionResult) -> ExecutionResult:
        return ExecutionResult(
            intent_id=result.intent_id,
            status=result.status,
            started_at=result.started_at,
            finished_at=result.finished_at,
            actual_result=result.actual_result,
            evidence_ids=result.evidence_ids,
            error=result.error,
            worker_pid=result.worker_pid,
            worker_create_time=result.worker_create_time,
            worker_return_code=result.worker_return_code,
            deduplicated=True,
        )
