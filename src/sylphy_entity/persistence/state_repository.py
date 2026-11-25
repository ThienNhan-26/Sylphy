"""Durable Entity, Brain, World, Goal and permission snapshots."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import uuid
from typing import Any

from sylphy_entity.contracts.decision import (
    Behaviour, BrainState, CognitionReceipt, DriveState, Goal, GoalStatus, NoveltyRecord,
)
from sylphy_entity.contracts.world import WorldSnapshot
from .codec import canonical_json, checksum_json, parse_json
from .database import EntityDatabase

_STATE_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def brain_state_to_dict(state: BrainState) -> dict[str, Any]:
    return {
        "behaviour": state.behaviour.value,
        "virtual_time": state.virtual_time,
        "drives": {
            "curiosity": state.drives.curiosity,
            "continuity": state.drives.continuity,
            "social_responsiveness": state.drives.social_responsiveness,
            "self_maintenance": state.drives.self_maintenance,
            "resource_conservation": state.drives.resource_conservation,
        },
        "goals": [
            {
                "goal_id": g.goal_id, "kind": g.kind, "subject": g.subject,
                "source": g.source, "priority": g.priority, "urgency": g.urgency,
                "desired_outcome": g.desired_outcome, "target": g.target,
                "status": g.status.value, "created_at_virtual": g.created_at_virtual,
                "updated_at_virtual": g.updated_at_virtual, "cost": g.cost,
                "risk": g.risk, "relevance": g.relevance,
                "required_permissions": list(g.required_permissions),
                "permission_scope": g.permission_scope,
                "deadline_at_virtual": g.deadline_at_virtual,
                "cooldown_until_virtual": g.cooldown_until_virtual,
                "failure_count": g.failure_count, "progress": g.progress,
                "completion_evidence": list(g.completion_evidence),
                "suspended_at_virtual": g.suspended_at_virtual,
                "resume_status": g.resume_status.value if g.resume_status else None,
                "attempt_count": g.attempt_count,
            }
            for g in state.goals
        ],
        "novelty": [
            {"subject": n.subject, "last_seen_virtual": n.last_seen_virtual,
             "visit_count": n.visit_count, "depth": n.depth}
            for n in state.novelty
        ],
        "yielded": state.yielded,
        "yielded_since_virtual": state.yielded_since_virtual,
        "transient_latches": list(state.transient_latches),
        "last_world_version": state.last_world_version,
        "last_reason": state.last_reason,
        "tick_count": state.tick_count,
        "cognition_requested_refs": list(state.cognition_requested_refs),
        "cognition_receipts": [
            {
                "request_id": item.request_id,
                "status": item.status,
                "accepted_as_evidence": item.accepted_as_evidence,
                "review_reason": item.review_reason,
                "source_refs": list(item.source_refs),
                "summary": item.summary,
                "findings": list(item.findings),
                "proposals": list(item.proposals),
                "model": item.model,
                "elapsed_ms": item.elapsed_ms,
                "error_code": item.error_code,
                "processed_at_virtual": item.processed_at_virtual,
            }
            for item in state.cognition_receipts
        ],
    }


def brain_state_from_dict(payload: dict[str, Any], *, restore_transients: bool = True) -> BrainState:
    goals: list[Goal] = []
    for item in payload.get("goals", []):
        values = dict(item)
        values["status"] = GoalStatus(values["status"])
        values["resume_status"] = (
            GoalStatus(values["resume_status"]) if values.get("resume_status") else None
        )
        for name in ("required_permissions", "completion_evidence"):
            values[name] = tuple(values.get(name, ()))
        goal = Goal(**values)
        if restore_transients and goal.status is GoalStatus.SUSPENDED:
            # A process pause is not an active user takeover after restart.
            # Resume the persistent goal without charging offline time.
            goal = replace(
                goal,
                status=goal.resume_status or GoalStatus.ACTIVE,
                suspended_at_virtual=None,
                resume_status=None,
            )
        goals.append(goal)
    novelty = tuple(NoveltyRecord(**item) for item in payload.get("novelty", []))
    behaviour = Behaviour(payload.get("behaviour", Behaviour.RESTING.value))
    if restore_transients and behaviour is Behaviour.YIELDING:
        behaviour = Behaviour.RECOVERING
    return BrainState(
        behaviour=behaviour,
        virtual_time=float(payload.get("virtual_time", 0.0)),
        drives=DriveState(**payload.get("drives", {})),
        goals=tuple(goals),
        novelty=novelty,
        yielded=False if restore_transients else bool(payload.get("yielded", False)),
        yielded_since_virtual=None if restore_transients else payload.get("yielded_since_virtual"),
        transient_latches=() if restore_transients else tuple(payload.get("transient_latches", ())),
        last_world_version=int(payload.get("last_world_version", -1)),
        last_reason=("restored_after_restart" if restore_transients else str(payload.get("last_reason", "initial"))),
        tick_count=int(payload.get("tick_count", 0)),
        cognition_requested_refs=tuple(payload.get("cognition_requested_refs", ())),
        cognition_receipts=tuple(
            CognitionReceipt(
                **{
                    **item,
                    "source_refs": tuple(item.get("source_refs", ())),
                    "findings": tuple(item.get("findings", ())),
                    "proposals": tuple(item.get("proposals", ())),
                }
            )
            for item in payload.get("cognition_receipts", ())
        ),
    )


class StateRepository:
    def __init__(self, database: EntityDatabase) -> None:
        self.database = database

    @property
    def entity_id(self) -> str:
        with self.database.read() as db:
            row = db.execute("SELECT value FROM entity_metadata WHERE key='entity_id'").fetchone()
        if row is None:
            raise RuntimeError("Entity identity has not been initialized by RuntimeStore")
        return str(row[0])

    def save_world_snapshot(
        self, snapshot: WorldSnapshot, *, event_sequence: int,
        projected_observation_id: str | None = None,
    ) -> None:
        payload = snapshot.model_dump(mode="json")
        payload_json = canonical_json(payload)
        checksum = checksum_json(payload)
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO world_snapshots(world_version,event_sequence,captured_at,payload_json,checksum_sha256)
                   VALUES(?,?,?,?,?)""",
                (snapshot.version, event_sequence, snapshot.captured_at.isoformat(), payload_json, checksum),
            )
            if projected_observation_id is not None:
                db.execute(
                    "UPDATE observations SET projected=1 WHERE observation_id=?",
                    (projected_observation_id,),
                )

    def load_latest_world_snapshot(self) -> tuple[WorldSnapshot | None, int]:
        with self.database.read() as db:
            row = db.execute(
                "SELECT event_sequence,payload_json,checksum_sha256 FROM world_snapshots "
                "ORDER BY snapshot_id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None, 0
        payload = parse_json(row[1])
        if checksum_json(payload) != row[2]:
            raise RuntimeError("persisted World Snapshot checksum mismatch")
        return WorldSnapshot.model_validate(payload), int(row[0])

    def save_brain_state(self, state: BrainState, *, metadata: dict[str, Any] | None = None) -> int:
        payload = {
            "schema_version": _STATE_SCHEMA_VERSION,
            "brain_state": brain_state_to_dict(state),
            "metadata": metadata or {},
        }
        payload_json = canonical_json(payload)
        checksum = checksum_json(payload)
        now = _now()
        with self.database.transaction() as db:
            cursor = db.execute(
                """INSERT INTO entity_state_snapshots(entity_id,schema_version,saved_at,payload_json,checksum_sha256)
                   VALUES(?,?,?,?,?)""",
                (self.entity_id, _STATE_SCHEMA_VERSION, now, payload_json, checksum),
            )
            for goal in state.goals:
                goal_payload = {
                    key: value for key, value in brain_state_to_dict(
                        replace(state, goals=(goal,))
                    )["goals"][0].items()
                }
                db.execute(
                    """INSERT INTO goals(goal_id,status,source,updated_at,payload_json)
                       VALUES(?,?,?,?,?) ON CONFLICT(goal_id) DO UPDATE SET
                       status=excluded.status,source=excluded.source,
                       updated_at=excluded.updated_at,payload_json=excluded.payload_json""",
                    (goal.goal_id, goal.status.value, goal.source, now, canonical_json(goal_payload)),
                )
            return int(cursor.lastrowid)

    def load_brain_state(self, *, restore_transients: bool = True) -> BrainState | None:
        with self.database.read() as db:
            row = db.execute(
                "SELECT payload_json,checksum_sha256 FROM entity_state_snapshots "
                "ORDER BY revision DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        payload = parse_json(row[0])
        if checksum_json(payload) != row[1]:
            raise RuntimeError("persisted Entity State checksum mismatch")
        if int(payload.get("schema_version", _STATE_SCHEMA_VERSION)) > _STATE_SCHEMA_VERSION:
            raise RuntimeError("persisted Entity State schema is newer than this runtime")
        return brain_state_from_dict(payload["brain_state"], restore_transients=restore_transients)

    def save_permission(
        self, permission_id: str, permission_class: str, permission_scope: str,
        *, state: str = "GRANTED", metadata: dict[str, Any] | None = None,
    ) -> None:
        if state not in {"GRANTED", "REVOKED"}:
            raise ValueError("permission state must be GRANTED or REVOKED")
        now = _now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO permissions(
                    permission_id,permission_class,permission_scope,state,granted_at,revoked_at,metadata_json
                ) VALUES(?,?,?,?,?,?,?) ON CONFLICT(permission_id) DO UPDATE SET
                state=excluded.state,revoked_at=excluded.revoked_at,metadata_json=excluded.metadata_json""",
                (permission_id,permission_class,permission_scope,state,now,
                 now if state == "REVOKED" else None,canonical_json(metadata or {})),
            )

    def load_permissions(self) -> tuple[dict[str, Any], ...]:
        with self.database.read() as db:
            rows = db.execute(
                "SELECT permission_id,permission_class,permission_scope,state,granted_at,revoked_at,metadata_json "
                "FROM permissions ORDER BY permission_id"
            ).fetchall()
        return tuple({
            "permission_id": row[0], "permission_class": row[1], "permission_scope": row[2],
            "state": row[3], "granted_at": row[4], "revoked_at": row[5],
            "metadata": parse_json(row[6]),
        } for row in rows)

    def latest_revision(self) -> int:
        with self.database.read() as db:
            row = db.execute("SELECT COALESCE(MAX(revision),0) FROM entity_state_snapshots").fetchone()
        return int(row[0])
