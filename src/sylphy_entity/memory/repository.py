"""Durable Entity-owned memory repository (Stage 7 memory foundation)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from uuid import uuid4

from sylphy_entity.contracts.memory import (
    MemoryCandidate,
    MemoryCandidateKind,
    MemoryCandidateStatus,
    MemoryOrigin,
    MemoryRevision,
    MemoryRecord,
    MemoryStatus,
)
from sylphy_entity.persistence.database import EntityDatabase


def _decode(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        memory_id=str(row["memory_id"]),
        content=str(row["content"]),
        category=str(row["category"]),
        origin=MemoryOrigin(row["origin"]),
        source_refs=tuple(json.loads(row["source_refs_json"])),
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        status=MemoryStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _decode_revision(row: sqlite3.Row) -> MemoryRevision:
    return MemoryRevision(
        revision_id=str(row["revision_id"]), memory_id=str(row["memory_id"]),
        action=str(row["action"]), content=str(row["content"]), category=str(row["category"]),
        origin=MemoryOrigin(row["origin"]), source_refs=tuple(json.loads(row["source_refs_json"])),
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        status=MemoryStatus(row["status"]), created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        changed_at=datetime.fromisoformat(row["changed_at"]),
        change_refs=tuple(json.loads(row["change_refs_json"])),
    )


class MemoryRepository:
    """Stores only explicitly confirmed/verified memories with traceable sources."""

    def __init__(self, database: EntityDatabase) -> None:
        self.database = database

    def create(self, memory: MemoryRecord) -> MemoryRecord:
        refs_json = json.dumps(list(memory.source_refs), ensure_ascii=False, separators=(",", ":"))
        with self.database.transaction() as db:
            existing = db.execute(
                "SELECT * FROM entity_memories WHERE memory_id=?", (memory.memory_id,)
            ).fetchone()
            if existing is not None:
                current = _decode(existing)
                if current != memory:
                    raise ValueError("memory_id was reused with different memory content")
                return current
            db.execute(
                """INSERT INTO entity_memories(
                    memory_id,content,category,origin,source_refs_json,confidence,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    memory.memory_id, memory.content, memory.category, memory.origin.value,
                    refs_json, memory.confidence, memory.status.value,
                    memory.created_at.isoformat(), memory.updated_at.isoformat(),
                ),
            )
        return memory

    def get(self, memory_id: str) -> MemoryRecord | None:
        with self.database.read() as db:
            row = db.execute(
                "SELECT * FROM entity_memories WHERE memory_id=?", (memory_id,)
            ).fetchone()
        return _decode(row) if row is not None else None

    def list_active(self, *, limit: int = 100) -> tuple[MemoryRecord, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self.database.read() as db:
            rows = db.execute(
                "SELECT * FROM entity_memories WHERE status='active' "
                "ORDER BY created_at,memory_id LIMIT ?", (limit,)
            ).fetchall()
        return tuple(_decode(row) for row in rows)

    def list_active_recent(self, *, limit: int = 500) -> tuple[MemoryRecord, ...]:
        """Return a bounded newest-first window for deterministic recall scans."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self.database.read() as db:
            rows = db.execute(
                "SELECT * FROM entity_memories WHERE status='active' "
                "ORDER BY created_at DESC,memory_id LIMIT ?", (limit,)
            ).fetchall()
        return tuple(_decode(row) for row in rows)

    def archive(self, memory_id: str, *, change_ref: str) -> bool:
        return self._change_status(memory_id, MemoryStatus.ARCHIVED, "archived", change_ref)

    def restore(self, memory_id: str, *, change_ref: str) -> bool:
        return self._change_status(memory_id, MemoryStatus.ACTIVE, "restored", change_ref)

    def archive_with_history(self, memory_id: str, *, change_ref: str) -> bool:
        return self.archive(memory_id, change_ref=change_ref)

    def _change_status(self, memory_id: str, target: MemoryStatus, action: str, change_ref: str) -> bool:
        ref = change_ref.strip()
        if not ref:
            raise ValueError("change_ref must not be blank")
        now = datetime.now(timezone.utc)
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM entity_memories WHERE memory_id=?", (memory_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown memory: {memory_id}")
            current = _decode(row)
            if current.status is target:
                return False
            if target is MemoryStatus.ACTIVE:
                duplicate = db.execute(
                    "SELECT memory_id FROM entity_memories WHERE status='active' AND memory_id<>? "
                    "AND lower(trim(category))=lower(trim(?)) AND lower(trim(content))=lower(trim(?)) LIMIT 1",
                    (memory_id, current.category, current.content),
                ).fetchone()
                if duplicate is not None:
                    raise ValueError("cannot restore: an exact active memory already exists")
            self._insert_revision(db, current, action=action, changed_at=now, change_refs=(ref,))
            db.execute(
                "UPDATE entity_memories SET status=?,updated_at=? WHERE memory_id=?",
                (target.value, now.isoformat(), memory_id),
            )
        return True

    def correct(
        self, memory_id: str, *, content: str, change_ref: str,
        category: str | None = None, confidence: float | None = None,
    ) -> MemoryRecord:
        if not isinstance(content, str) or not content.strip() or len(content) > 8000:
            raise ValueError("corrected content must contain 1..8000 characters")
        ref = change_ref.strip()
        if not ref:
            raise ValueError("change_ref must not be blank")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        now = datetime.now(timezone.utc)
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM entity_memories WHERE memory_id=?", (memory_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown memory: {memory_id}")
            current = _decode(row)
            target_category = category.strip() if category is not None else current.category
            if not target_category or len(target_category) > 80:
                raise ValueError("corrected category must contain 1..80 characters")
            target_confidence = confidence if confidence is not None else current.confidence
            if (content.strip(), target_category, target_confidence) == (
                current.content, current.category, current.confidence
            ):
                return current
            duplicate = db.execute(
                "SELECT memory_id FROM entity_memories WHERE status='active' AND memory_id<>? "
                "AND lower(trim(category))=lower(trim(?)) AND lower(trim(content))=lower(trim(?)) LIMIT 1",
                (memory_id, target_category, content.strip()),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("an exact active memory with this category and content already exists")
            self._insert_revision(db, current, action="corrected", changed_at=now, change_refs=(ref,))
            db.execute(
                "UPDATE entity_memories SET content=?,category=?,confidence=?,updated_at=? WHERE memory_id=?",
                (content.strip(), target_category, target_confidence, now.isoformat(), memory_id),
            )
        result = self.get(memory_id)
        if result is None:
            raise RuntimeError("corrected memory disappeared")
        return result

    @staticmethod
    def _insert_revision(db: sqlite3.Connection, memory: MemoryRecord, *, action: str,
                         changed_at: datetime, change_refs: tuple[str, ...]) -> None:
        db.execute(
            """INSERT INTO entity_memory_revisions(
                revision_id,memory_id,action,content,category,origin,source_refs_json,confidence,
                status,created_at,updated_at,changed_at,change_refs_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid4()), memory.memory_id, action, memory.content, memory.category,
             memory.origin.value, json.dumps(list(memory.source_refs), ensure_ascii=False),
             memory.confidence, memory.status.value, memory.created_at.isoformat(),
             memory.updated_at.isoformat(), changed_at.isoformat(),
             json.dumps(list(change_refs), ensure_ascii=False)),
        )

    def history(self, memory_id: str, *, limit: int = 100) -> tuple[MemoryRevision, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self.database.read() as db:
            rows = db.execute(
                "SELECT * FROM entity_memory_revisions WHERE memory_id=? "
                "ORDER BY changed_at,revision_id LIMIT ?", (memory_id, limit)
            ).fetchall()
        return tuple(_decode_revision(row) for row in rows)


def _decode_candidate(row: sqlite3.Row) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=str(row["candidate_id"]),
        content=str(row["content"]),
        category=str(row["category"]),
        kind=MemoryCandidateKind(row["kind"]),
        source_refs=tuple(json.loads(row["source_refs_json"])),
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        status=MemoryCandidateStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        decision_ref=row["decision_ref"],
        resulting_memory_id=row["resulting_memory_id"],
    )


class MemoryCaptureService:
    """Stages untrusted candidates and promotes them only through explicit gates."""

    def __init__(self, database: EntityDatabase) -> None:
        self.database = database

    def capture(self, candidate: MemoryCandidate) -> MemoryCandidate:
        """Persist a pending proposal; capture never creates an active memory."""
        refs_json = json.dumps(list(candidate.source_refs), ensure_ascii=False, separators=(",", ":"))
        with self.database.transaction() as db:
            existing = db.execute(
                "SELECT * FROM entity_memory_candidates WHERE candidate_id=?", (candidate.candidate_id,)
            ).fetchone()
            if existing is not None:
                current = _decode_candidate(existing)
                if current != candidate:
                    raise ValueError("candidate_id was reused with different candidate content")
                return current
            if candidate.status is not MemoryCandidateStatus.PENDING:
                raise ValueError("new memory candidates must start pending")
            db.execute(
                """INSERT INTO entity_memory_candidates(
                    candidate_id,content,category,kind,source_refs_json,confidence,status,
                    created_at,updated_at,decision_ref,resulting_memory_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (candidate.candidate_id, candidate.content, candidate.category, candidate.kind.value,
                 refs_json, candidate.confidence, candidate.status.value,
                 candidate.created_at.isoformat(), candidate.updated_at.isoformat(), None, None),
            )
        return candidate

    def get_candidate(self, candidate_id: str) -> MemoryCandidate | None:
        with self.database.read() as db:
            row = db.execute(
                "SELECT * FROM entity_memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
        return _decode_candidate(row) if row is not None else None

    def list_pending(self, *, limit: int = 100) -> tuple[MemoryCandidate, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self.database.read() as db:
            rows = db.execute(
                "SELECT * FROM entity_memory_candidates WHERE status='pending' "
                "ORDER BY created_at,candidate_id LIMIT ?", (limit,)
            ).fetchall()
        return tuple(_decode_candidate(row) for row in rows)

    def confirm_user_statement(self, candidate_id: str, *, user_confirmation_ref: str) -> MemoryRecord:
        return self._confirm(
            candidate_id, origin=MemoryOrigin.USER_CONFIRMED,
            required_kind=MemoryCandidateKind.USER_STATEMENT,
            decision_refs=(user_confirmation_ref,),
        )

    def confirm_cognition_proposal(
        self, candidate_id: str, *, brain_review_ref: str, user_confirmation_ref: str
    ) -> MemoryRecord:
        return self._confirm(
            candidate_id, origin=MemoryOrigin.COGNITION_CONFIRMED,
            required_kind=MemoryCandidateKind.COGNITION_PROPOSAL,
            decision_refs=(brain_review_ref, user_confirmation_ref),
        )

    def confirm_verified_import(
        self, candidate_id: str, *, verification_ref: str, user_confirmation_ref: str
    ) -> MemoryRecord:
        return self._confirm(
            candidate_id, origin=MemoryOrigin.IMPORTED_VERIFIED,
            required_kind=MemoryCandidateKind.EXTERNAL_IMPORT,
            decision_refs=(verification_ref, user_confirmation_ref),
        )

    def _confirm(
        self, candidate_id: str, *, origin: MemoryOrigin,
        required_kind: MemoryCandidateKind, decision_refs: tuple[str, ...],
    ) -> MemoryRecord:
        refs = tuple(ref.strip() for ref in decision_refs)
        if any(not ref for ref in refs):
            raise ValueError("confirmation and review references must not be blank")
        now = datetime.now(timezone.utc)
        with self.database.transaction() as db:
            row = db.execute(
                "SELECT * FROM entity_memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown memory candidate: {candidate_id}")
            candidate = _decode_candidate(row)
            if candidate.status is MemoryCandidateStatus.CONFIRMED:
                memory_row = db.execute(
                    "SELECT * FROM entity_memories WHERE memory_id=?", (candidate.resulting_memory_id,)
                ).fetchone()
                if memory_row is None:
                    raise RuntimeError("confirmed candidate references a missing memory record")
                return _decode(memory_row)
            if candidate.status is not MemoryCandidateStatus.PENDING:
                raise ValueError("only pending candidates can be confirmed")
            if candidate.kind is not required_kind:
                raise ValueError(f"candidate kind {candidate.kind.value} cannot use this confirmation path")
            source_refs = tuple(dict.fromkeys((*candidate.source_refs, *refs)))
            if len(source_refs) > 32:
                raise ValueError("combined provenance exceeds 32 source references")
            memory = MemoryRecord(
                content=candidate.content, category=candidate.category, origin=origin,
                source_refs=source_refs, confidence=candidate.confidence,
                created_at=candidate.created_at, updated_at=now,
            )
            db.execute(
                """INSERT INTO entity_memories(
                    memory_id,content,category,origin,source_refs_json,confidence,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (memory.memory_id, memory.content, memory.category, memory.origin.value,
                 json.dumps(list(memory.source_refs), ensure_ascii=False, separators=(",", ":")),
                 memory.confidence, memory.status.value, memory.created_at.isoformat(), memory.updated_at.isoformat()),
            )
            db.execute(
                "UPDATE entity_memory_candidates SET status='confirmed',updated_at=?,decision_ref=?,resulting_memory_id=? "
                "WHERE candidate_id=? AND status='pending'",
                (now.isoformat(), json.dumps(list(refs), ensure_ascii=False), memory.memory_id, candidate_id),
            )
        return memory

    def reject(self, candidate_id: str, *, decision_ref: str) -> MemoryCandidate:
        decision_ref = decision_ref.strip()
        if not decision_ref:
            raise ValueError("rejection decision_ref must not be blank")
        now = datetime.now(timezone.utc)
        with self.database.transaction() as db:
            row = db.execute(
                "SELECT * FROM entity_memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown memory candidate: {candidate_id}")
            candidate = _decode_candidate(row)
            if candidate.status is MemoryCandidateStatus.REJECTED:
                return candidate
            if candidate.status is not MemoryCandidateStatus.PENDING:
                raise ValueError("only pending candidates can be rejected")
            db.execute(
                "UPDATE entity_memory_candidates SET status='rejected',updated_at=?,decision_ref=? "
                "WHERE candidate_id=? AND status='pending'",
                (now.isoformat(), decision_ref, candidate_id),
            )
            updated = db.execute(
                "SELECT * FROM entity_memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
        return _decode_candidate(updated)
