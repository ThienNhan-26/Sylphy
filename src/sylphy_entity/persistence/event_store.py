"""Append-only event journal and durable Intent/Execution ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

from sylphy_entity.contracts.events import EntityEvent
from sylphy_entity.contracts.execution import ExecutionResult, ExecutionStatus, Intent, ResourceLease
from sylphy_entity.contracts.observation import Observation
from .codec import canonical_json, parse_json
from .database import EntityDatabase


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _intent_payload(intent: Intent) -> dict[str, Any]:
    """Persist monotonic values as original diagnostics, never as restart clocks."""
    return {
        "intent_id": intent.intent_id,
        "goal_id": intent.goal_id,
        "capability": intent.capability,
        "target": intent.target,
        "arguments": [list(item) for item in intent.arguments],
        "reason": intent.reason,
        "preconditions": list(intent.preconditions),
        "permission_class": intent.permission_class.value,
        "permission_scope": intent.permission_scope,
        "resources": [
            {"resource_key": item.resource_key, "mode": item.mode.value}
            for item in intent.resources
        ],
        "priority": intent.priority,
        "expected_outcome": intent.expected_outcome,
        "idempotency_key": intent.idempotency_key,
        # Monotonic clocks are process-local and must never be replayed after
        # restart. Persist only the relative budget as a diagnostic/policy value.
        "deadline_after_seconds": (
            round(intent.deadline_at_monotonic - intent.created_at_monotonic, 6)
            if intent.deadline_at_monotonic is not None else None
        ),
        "timeout_seconds": intent.timeout_seconds,
        "cancellation_policy": intent.cancellation_policy.value,
        "origin": intent.origin.value,
        "source_observation_id": intent.source_observation_id,
    }


@dataclass(frozen=True, slots=True)
class AppendResult:
    sequence: int
    inserted: bool


@dataclass(frozen=True, slots=True)
class StoredEvent:
    sequence: int
    event: EntityEvent
    recorded_at: datetime


class IdempotencyConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PersistedIntentLookup:
    status: str
    result: ExecutionResult | None


class EventStore:
    def __init__(self, database: EntityDatabase) -> None:
        self.database = database

    def append(self, event: EntityEvent, *, correlation_id: str | None = None) -> AppendResult:
        payload = canonical_json(event.payload)
        now = _now()
        with self.database.transaction() as db:
            row = db.execute(
                "SELECT sequence, event_type, source, occurred_at, payload_json "
                "FROM entity_events WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
            if row is not None:
                if (row[1], row[2], row[3], row[4]) != (
                    event.event_type, event.source, event.occurred_at.isoformat(), payload
                ):
                    raise ValueError("event_id was reused with different event content")
                return AppendResult(int(row[0]), False)
            cursor = db.execute(
                """INSERT INTO entity_events(
                    event_id,event_type,source,occurred_at,recorded_at,payload_json,correlation_id
                ) VALUES(?,?,?,?,?,?,?)""",
                (event.event_id, event.event_type, event.source, event.occurred_at.isoformat(), now, payload, correlation_id),
            )
            return AppendResult(int(cursor.lastrowid), True)

    def append_observation(self, observation: Observation, event_sequence: int) -> bool:
        """Persist provenance and say whether projection is still required.

        A row left at projected=0 is deliberately replayable after a crash
        between observation receipt and World Snapshot commit.
        """
        payload = canonical_json(observation.model_dump(mode="json"))
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO observations(
                    observation_id,event_sequence,source_id,subject,kind,observed_at,received_at,payload_json,projected
                ) VALUES(?,?,?,?,?,?,?,?,0) ON CONFLICT(observation_id) DO NOTHING""",
                (
                    observation.observation_id, event_sequence, observation.source_id,
                    observation.subject, observation.kind, observation.observed_at.isoformat(),
                    observation.received_at.isoformat(), payload,
                ),
            )
            row = db.execute(
                "SELECT event_sequence,source_id,subject,kind,observed_at,received_at,payload_json,projected "
                "FROM observations WHERE observation_id=?",
                (observation.observation_id,),
            ).fetchone()
            # The first event sequence is the canonical provenance for this
            # Observation ID. Re-delivery under another event ID is an
            # idempotent duplicate when the Observation payload is identical.
            # Reusing the ID for changed facts remains a hard integrity error.
            expected_content = (
                observation.source_id, observation.subject, observation.kind,
                observation.observed_at.isoformat(), observation.received_at.isoformat(), payload,
            )
            if tuple(row[1:7]) != expected_content:
                raise ValueError("observation_id was reused with different observation content or provenance")
            return not bool(row[7])

    def sequence_for(self, event_id: str) -> int | None:
        with self.database.read() as db:
            row = db.execute("SELECT sequence FROM entity_events WHERE event_id=?", (event_id,)).fetchone()
            return int(row[0]) if row else None

    def latest_sequence(self) -> int:
        with self.database.read() as db:
            row = db.execute("SELECT COALESCE(MAX(sequence),0) FROM entity_events").fetchone()
            return int(row[0])

    def read_after(self, sequence: int = 0, *, limit: int = 1000) -> tuple[StoredEvent, ...]:
        if sequence < 0 or limit < 1:
            raise ValueError("sequence must be non-negative and limit positive")
        with self.database.read() as db:
            rows = db.execute(
                "SELECT sequence,event_id,event_type,source,occurred_at,recorded_at,payload_json "
                "FROM entity_events WHERE sequence>? ORDER BY sequence LIMIT ?",
                (sequence, limit),
            ).fetchall()
        result = []
        for row in rows:
            result.append(
                StoredEvent(
                    sequence=int(row[0]),
                    event=EntityEvent(
                        event_id=row[1], event_type=row[2], source=row[3],
                        occurred_at=datetime.fromisoformat(row[4]), payload=parse_json(row[6]),
                    ),
                    recorded_at=datetime.fromisoformat(row[5]),
                )
            )
        return tuple(result)

    def record_intent(self, intent: Intent) -> None:
        payload = _intent_payload(intent)
        payload_json = canonical_json(payload)
        fingerprint = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        now = _now()
        with self.database.transaction() as db:
            existing = db.execute(
                "SELECT intent_id,fingerprint_sha256,status FROM intents WHERE idempotency_key=?",
                (intent.idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing[1] != fingerprint:
                    raise IdempotencyConflict("idempotency key reused with different persisted Intent")
                if existing[0] != intent.intent_id:
                    raise IdempotencyConflict("idempotency key already belongs to another Intent")
                if existing[2] in {"BLOCKED", "REJECTED"}:
                    # These outcomes did not cross a side-effect boundary and are
                    # retryable after resources/permissions change. Keep prior
                    # execution attempts as history but reopen the Intent record.
                    db.execute(
                        "UPDATE intents SET status='ACCEPTED',updated_at=?,error=NULL WHERE intent_id=?",
                        (now, intent.intent_id),
                    )
                    self._append_internal_event(db, "execution.intent_rearmed", intent.intent_id, {
                        "intent_id": intent.intent_id, "previous_status": existing[2],
                    })
                    return
                raise RuntimeError(
                    f"persisted Intent already exists in state {existing[2]}; reconcile before dispatch"
                )
            db.execute(
                """INSERT INTO intents(
                    intent_id,idempotency_key,goal_id,status,permission_scope,fingerprint_sha256,
                    created_at,updated_at,payload_json,error
                ) VALUES(?,?,?,'ACCEPTED',?,?,?,?,?,NULL)""",
                (intent.intent_id, intent.idempotency_key, intent.goal_id, intent.permission_scope,
                 fingerprint, now, now, payload_json),
            )
            self._append_internal_event(db, "execution.intent_accepted", intent.intent_id, {"intent_id": intent.intent_id})

    def lookup_intent(self, intent: Intent) -> PersistedIntentLookup | None:
        """Look up durable idempotency before any new dispatch is admitted."""
        payload_json = canonical_json(_intent_payload(intent))
        fingerprint = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self.database.read() as db:
            row = db.execute(
                "SELECT intent_id,fingerprint_sha256,status FROM intents WHERE idempotency_key=?",
                (intent.idempotency_key,),
            ).fetchone()
            if row is None:
                return None
            if row[0] != intent.intent_id or row[1] != fingerprint:
                raise IdempotencyConflict("idempotency key reused with a different persisted Intent")
            status = str(row[2])
            if status in {"BLOCKED", "REJECTED"}:
                # These are known no-side-effect outcomes and may be retried.
                return None
            execution = db.execute(
                """SELECT status,started_at,finished_at,worker_pid,worker_create_time,
                          worker_return_code,actual_result_json,evidence_ids_json,error
                   FROM executions WHERE intent_id=? ORDER BY attempt DESC LIMIT 1""",
                (intent.intent_id,),
            ).fetchone()
        if execution is None or execution[2] is None:
            return PersistedIntentLookup(status=status, result=None)
        persisted_status = status
        if persisted_status == "INTERRUPTED":
            persisted_status = ExecutionStatus.UNKNOWN.value.upper()
        try:
            result_status = ExecutionStatus(persisted_status.lower())
        except ValueError:
            result_status = ExecutionStatus.UNKNOWN
        result = ExecutionResult(
            intent_id=intent.intent_id, status=result_status,
            started_at=datetime.fromisoformat(execution[1]),
            finished_at=datetime.fromisoformat(execution[2]),
            worker_pid=execution[3], worker_create_time=execution[4],
            worker_return_code=execution[5], actual_result=parse_json(execution[6]),
            evidence_ids=tuple(parse_json(execution[7])), error=execution[8], deduplicated=True,
        )
        return PersistedIntentLookup(status=status, result=result)

    def mark_intent_dispatching(self, intent: Intent) -> str:
        now = _now()
        execution_id = uuid4().hex
        with self.database.transaction() as db:
            row = db.execute("SELECT status FROM intents WHERE intent_id=?", (intent.intent_id,)).fetchone()
            if row is None:
                raise KeyError(f"Intent not persisted before dispatch: {intent.intent_id}")
            if row[0] != "ACCEPTED":
                raise RuntimeError(f"Intent cannot dispatch from persisted state {row[0]}")
            attempt = int(db.execute(
                "SELECT COALESCE(MAX(attempt),0)+1 FROM executions WHERE intent_id=?",
                (intent.intent_id,),
            ).fetchone()[0])
            db.execute(
                "UPDATE intents SET status='DISPATCHING',updated_at=? WHERE intent_id=?",
                (now, intent.intent_id),
            )
            db.execute(
                """INSERT INTO executions(
                    execution_id,intent_id,attempt,status,started_at,actual_result_json,evidence_ids_json
                ) VALUES(?,?,?,'DISPATCHING',?,'{}','[]')""",
                (execution_id, intent.intent_id, attempt, now),
            )
            self._append_internal_event(db, "execution.intent_dispatching", intent.intent_id, {
                "intent_id": intent.intent_id, "execution_id": execution_id, "attempt": attempt,
            })
        return execution_id

    def record_worker_started(
        self, *, intent_id: str, pid: int, create_time: float | None,
        daemon_instance_id: str, worker_name: str,
    ) -> str:
        worker_id = f"{intent_id}:{pid}:{create_time if create_time is not None else 'unknown'}"
        with self.database.transaction() as db:
            db.execute(
                """INSERT OR REPLACE INTO worker_processes(
                    worker_id,intent_id,pid,create_time,daemon_instance_id,started_at,state,metadata_json
                ) VALUES(?,?,?,?,?,?,'RUNNING',?)""",
                (worker_id, intent_id, pid, create_time, daemon_instance_id, _now(), canonical_json({"name": worker_name})),
            )
        return worker_id

    def record_worker_finished(
        self, *, pid: int, create_time: float | None, return_code: int, state: str,
    ) -> int:
        """Persist the terminal state of every supervised worker identity."""
        normalized = state.upper()
        if normalized not in {"EXITED", "FAILED", "CANCELLED"}:
            raise ValueError(f"unsupported terminal worker state: {state}")
        now = _now()
        with self.database.transaction() as db:
            if create_time is None:
                cursor = db.execute(
                    "UPDATE worker_processes SET state=?,ended_at=?,return_code=? "
                    "WHERE pid=? AND create_time IS NULL AND state='RUNNING'",
                    (normalized, now, return_code, pid),
                )
            else:
                cursor = db.execute(
                    "UPDATE worker_processes SET state=?,ended_at=?,return_code=? "
                    "WHERE pid=? AND create_time=? AND state='RUNNING'",
                    (normalized, now, return_code, pid, create_time),
                )
            updated = int(cursor.rowcount)
            if updated:
                self._append_internal_event(db, "runtime.worker_finished", str(pid), {
                    "pid": pid, "create_time": create_time, "return_code": return_code,
                    "state": normalized,
                })
            return updated

    def record_leases(self, intent_id: str, leases: tuple[ResourceLease, ...]) -> None:
        now = datetime.now(timezone.utc)
        with self.database.transaction() as db:
            for lease in leases:
                ttl = max(0.0, lease.expires_at_monotonic - lease.acquired_at_monotonic)
                acquired = now
                expires = now.timestamp() + ttl
                db.execute(
                    """INSERT OR REPLACE INTO resource_leases(
                        lease_id,intent_id,resource_key,mode,acquired_at,expires_at,released_at,state,payload_json
                    ) VALUES(?,?,?,?,?,?,NULL,'ACTIVE',?)""",
                    (lease.lease_id,intent_id,lease.resource_key,lease.mode.value,acquired.isoformat(),
                     datetime.fromtimestamp(expires, timezone.utc).isoformat(),
                     canonical_json({"owner_id": lease.owner_id, "ttl_seconds": ttl})),
                )

    def release_leases(self, leases: tuple[ResourceLease, ...], *, state: str = "RELEASED") -> None:
        now = _now()
        if not leases:
            return
        with self.database.transaction() as db:
            db.executemany(
                "UPDATE resource_leases SET state=?,released_at=? WHERE lease_id=?",
                [(state, now, lease.lease_id) for lease in leases],
            )

    def finish_execution(self, intent: Intent, result: ExecutionResult) -> None:
        now = _now()
        status = result.status.value.upper()
        with self.database.transaction() as db:
            execution = db.execute(
                "SELECT execution_id FROM executions WHERE intent_id=? AND finished_at IS NULL "
                "ORDER BY attempt DESC LIMIT 1", (intent.intent_id,),
            ).fetchone()
            if execution is None:
                # Permission/deadline/validation rejection can happen before a dispatch row exists.
                execution_id = uuid4().hex
                attempt = int(db.execute(
                    "SELECT COALESCE(MAX(attempt),0)+1 FROM executions WHERE intent_id=?",
                    (intent.intent_id,),
                ).fetchone()[0])
                db.execute(
                    """INSERT INTO executions(
                        execution_id,intent_id,attempt,status,started_at,finished_at,worker_pid,
                        worker_create_time,worker_return_code,actual_result_json,evidence_ids_json,error
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (execution_id,intent.intent_id,attempt,status,result.started_at.isoformat(),
                     result.finished_at.isoformat(),result.worker_pid,result.worker_create_time,
                     result.worker_return_code,canonical_json(dict(result.actual_result)),
                     canonical_json(result.evidence_ids),result.error),
                )
            else:
                db.execute(
                    """UPDATE executions SET status=?,finished_at=?,worker_pid=?,worker_create_time=?,
                       worker_return_code=?,actual_result_json=?,evidence_ids_json=?,error=?
                       WHERE execution_id=?""",
                    (status,result.finished_at.isoformat(),result.worker_pid,result.worker_create_time,
                     result.worker_return_code,canonical_json(dict(result.actual_result)),
                     canonical_json(result.evidence_ids),result.error,execution[0]),
                )
            db.execute(
                "UPDATE intents SET status=?,updated_at=?,error=? WHERE intent_id=?",
                (status, now, result.error, intent.intent_id),
            )
            if result.worker_pid is not None:
                db.execute(
                    """UPDATE worker_processes SET state='EXITED',ended_at=?,return_code=?
                       WHERE intent_id=? AND pid=? AND state='RUNNING'""",
                    (now,result.worker_return_code,intent.intent_id,result.worker_pid),
                )
            for evidence_id in result.evidence_ids:
                actual = dict(result.actual_result)
                digest = actual.get("content_sha256") or actual.get("sha256")
                db.execute(
                    """INSERT INTO evidence(evidence_id,intent_id,kind,sha256,verified_at,metadata_json)
                       VALUES(?,?,?,?,?,?) ON CONFLICT(evidence_id) DO NOTHING""",
                    (evidence_id,intent.intent_id,"execution_result",digest if isinstance(digest,str) else None,
                     now,canonical_json(actual)),
                )
            self._append_internal_event(db, "execution.result", intent.intent_id, {
                "intent_id": intent.intent_id, "status": status,
                "evidence_ids": list(result.evidence_ids), "error": result.error,
            })

    def list_successful_intent_results(self) -> tuple[dict[str, Any], ...]:
        """Return verified terminal successes for Stage-6 goal-feedback recovery."""
        with self.database.transaction() as db:
            rows = db.execute(
                """SELECT i.intent_id, i.goal_id, e.evidence_ids_json, e.finished_at
                   FROM intents i JOIN executions e ON e.intent_id=i.intent_id
                   WHERE i.status='SUCCEEDED' AND e.status='SUCCEEDED' AND e.finished_at IS NOT NULL
                   ORDER BY e.finished_at, e.attempt"""
            ).fetchall()
        return tuple({
            "intent_id": row[0],
            "goal_id": row[1],
            "evidence_ids": tuple(parse_json(row[2])),
            "finished_at": row[3],
        } for row in rows)

    def list_incomplete_intents(self) -> tuple[dict[str, Any], ...]:
        with self.database.read() as db:
            rows = db.execute(
                "SELECT intent_id,idempotency_key,goal_id,status,permission_scope,payload_json "
                "FROM intents WHERE status IN ('ACCEPTED','DISPATCHING','RUNNING') ORDER BY created_at"
            ).fetchall()
        return tuple({
            "intent_id": row[0], "idempotency_key": row[1], "goal_id": row[2],
            "status": row[3], "permission_scope": row[4], "payload": parse_json(row[5]),
        } for row in rows)

    def list_unknown_intent_targets(self, *, permission_scope: str | None = None) -> tuple[dict[str, Any], ...]:
        """Return unresolved autonomous file targets that must not be retried blindly.

        An UNKNOWN execution may have crossed its side-effect boundary. For the
        Stage-6 read-only metadata capability, keep the exact target revision in
        quarantine until a later observation proves that the file changed or
        disappeared. A durable superseded event prevents the quarantine from
        reappearing after another daemon restart.
        """
        query = (
            "SELECT i.intent_id,i.payload_json FROM intents i "
            "WHERE i.status='UNKNOWN' AND NOT EXISTS ("
            " SELECT 1 FROM entity_events e "
            " WHERE e.event_type='execution.unknown_target_superseded' "
            " AND e.source=i.intent_id) ORDER BY i.created_at"
        )
        with self.database.read() as db:
            rows = db.execute(query).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            payload = parse_json(row[1])
            if not isinstance(payload, dict):
                continue
            if payload.get("origin") != "autonomous":
                continue
            if payload.get("capability") != "inspect_file_metadata":
                continue
            target = payload.get("target")
            scope = payload.get("permission_scope")
            if not isinstance(target, str) or not target.strip():
                continue
            if permission_scope is not None and scope != permission_scope:
                continue
            records.append({
                "intent_id": row[0], "goal_id": payload.get("goal_id"),
                "target": target, "permission_scope": scope,
            })
        return tuple(records)

    def mark_unknown_target_superseded(
        self, intent_id: str, *, target: str, reason: str, revision: tuple[Any, ...] | None = None,
    ) -> bool:
        """Durably release an UNKNOWN target after observation proves it is no longer the same work."""
        with self.database.transaction() as db:
            row = db.execute(
                "SELECT status FROM intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None or row[0] != "UNKNOWN":
                return False
            existing = db.execute(
                "SELECT 1 FROM entity_events WHERE event_type=? AND source=? LIMIT 1",
                ("execution.unknown_target_superseded", intent_id),
            ).fetchone()
            if existing is not None:
                return False
            self._append_internal_event(db, "execution.unknown_target_superseded", intent_id, {
                "intent_id": intent_id,
                "target": target,
                "reason": reason,
                "observed_revision": list(revision) if revision is not None else None,
            })
            return True

    def mark_intent_recovered(self, intent_id: str, status: str, reason: str) -> None:
        now = _now()
        with self.database.transaction() as db:
            db.execute(
                "UPDATE intents SET status=?,updated_at=?,error=? WHERE intent_id=?",
                (status, now, reason, intent_id),
            )
            db.execute(
                """UPDATE executions SET status='UNKNOWN',finished_at=?,error=?
                   WHERE intent_id=? AND finished_at IS NULL""",
                (now, reason, intent_id),
            )
            self._append_internal_event(db, "execution.reconciled", intent_id, {
                "intent_id": intent_id, "status": status, "reason": reason,
            })

    def active_workers(self) -> tuple[dict[str, Any], ...]:
        with self.database.read() as db:
            rows = db.execute(
                "SELECT worker_id,intent_id,pid,create_time,daemon_instance_id,started_at,metadata_json "
                "FROM worker_processes WHERE state='RUNNING'"
            ).fetchall()
        return tuple({
            "worker_id": row[0], "intent_id": row[1], "pid": int(row[2]),
            "create_time": row[3], "daemon_instance_id": row[4],
            "started_at": row[5], "metadata": parse_json(row[6]),
        } for row in rows)

    def mark_worker_recovered(self, worker_id: str, state: str) -> None:
        with self.database.transaction() as db:
            db.execute(
                "UPDATE worker_processes SET state=?,ended_at=? WHERE worker_id=? AND state='RUNNING'",
                (state, _now(), worker_id),
            )

    def mark_active_leases_abandoned(self) -> int:
        with self.database.transaction() as db:
            cursor = db.execute(
                "UPDATE resource_leases SET state='ABANDONED',released_at=? WHERE state='ACTIVE'",
                (_now(),),
            )
            return int(cursor.rowcount)

    @staticmethod
    def _append_internal_event(db, event_type: str, source: str, payload: dict[str, Any]) -> None:
        event_id = uuid4().hex
        db.execute(
            """INSERT INTO entity_events(
                event_id,event_type,source,occurred_at,recorded_at,payload_json
            ) VALUES(?,?,?,?,?,?)""",
            (event_id,event_type,source,_now(),_now(),canonical_json(payload)),
        )
