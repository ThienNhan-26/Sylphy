"""Read-only preview and explicitly staged import of frozen legacy memories.

No legacy row is written to Entity memory by preview. Staging creates pending
candidates only; a separate user-confirmation and verification gate is required
before a candidate can become an active MemoryRecord.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from sylphy_entity.contracts.memory import MemoryCandidate, MemoryCandidateKind
from sylphy_entity.memory.repository import MemoryCaptureService


@dataclass(frozen=True, slots=True)
class LegacyImportPreview:
    source_fingerprint: str
    candidates: tuple[MemoryCandidate, ...]
    skipped: tuple[tuple[str, str], ...]


class LegacyMemoryImporter:
    """Allowlisted, read-only adapter for the frozen `src/sylphy` SQLite schema."""

    def preview(self, database_path: Path, *, limit: int = 500) -> LegacyImportPreview:
        path = Path(database_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError("legacy database path must identify a file")
        if not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        uri = path.as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
            ).fetchone()
            if table is None:
                raise ValueError("legacy database has no allowlisted memories table")
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(memories)")}
            required = {"id", "content", "category", "status"}
            if not required.issubset(columns):
                raise ValueError("legacy memories table is missing required columns")
            optional = {
                name: name in columns for name in (
                    "created_at", "updated_at", "confidence", "origin",
                    "source_session_id", "source_message_id", "source_message_ids",
                )
            }
            select_columns = ["id", "content", "category", "status"] + [
                name for name, exists in optional.items() if exists
            ]
            sql = "SELECT " + ",".join(f'"{name}"' for name in select_columns)
            sql += " FROM memories WHERE status='confirmed' ORDER BY id LIMIT ?"
            rows = connection.execute(sql, (limit,)).fetchall()
            snapshot_payload = {
                "source": str(path),
                "rows": [dict(row) for row in rows],
            }
            fingerprint = hashlib.sha256(
                json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True, default=str,
                           separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        finally:
            connection.close()

        candidates: list[MemoryCandidate] = []
        skipped: list[tuple[str, str]] = []
        for row in rows:
            legacy_id = str(row["id"] or "").strip()
            try:
                content = str(row["content"] or "").strip()
                category = str(row["category"] or "other").strip() or "other"
                if not legacy_id or not content or len(content) > 8000 or len(category) > 80:
                    raise ValueError("blank ID/content or field exceeds Entity limits")
                refs = [f"legacy-db-sha256:{fingerprint}", f"legacy-memory:{legacy_id}"]
                if optional["origin"] and row["origin"]:
                    refs.append(f"legacy-origin:{str(row['origin'])[:80]}")
                if optional["source_session_id"] and row["source_session_id"]:
                    refs.append(f"legacy-session:{str(row['source_session_id'])}")
                if optional["source_message_ids"] and row["source_message_ids"]:
                    try:
                        message_ids = json.loads(str(row["source_message_ids"]))
                    except (TypeError, json.JSONDecodeError):
                        message_ids = []
                    if isinstance(message_ids, list):
                        refs.extend(f"legacy-message:{item}" for item in message_ids if isinstance(item, str) and item.strip())
                if optional["source_message_id"] and row["source_message_id"]:
                    refs.append(f"legacy-message:{str(row['source_message_id'])}")
                refs = list(dict.fromkeys(refs))
                if len(refs) > 32:
                    raise ValueError("legacy row has more than 32 provenance references")
                confidence = row["confidence"] if optional["confidence"] else None
                confidence = float(confidence) if confidence is not None else None
                if confidence is not None and not 0.0 <= confidence <= 1.0:
                    confidence = None
                created = _parse_timestamp(row["created_at"] if optional["created_at"] else None) or datetime.now(timezone.utc)
                updated = _parse_timestamp(row["updated_at"] if optional["updated_at"] else None) or created
                candidate_id = str(uuid5(NAMESPACE_URL, f"sylphy-legacy-import:{fingerprint}:{legacy_id}"))
                candidates.append(MemoryCandidate(
                    candidate_id=candidate_id, content=content, category=category,
                    kind=MemoryCandidateKind.EXTERNAL_IMPORT, source_refs=tuple(refs),
                    confidence=confidence, created_at=created, updated_at=updated or created,
                ))
            except (ValueError, TypeError, OverflowError) as exc:
                skipped.append((legacy_id or "<unknown>", str(exc)))
        return LegacyImportPreview(fingerprint, tuple(candidates), tuple(skipped))

    def stage(self, preview: LegacyImportPreview, capture: MemoryCaptureService) -> tuple[MemoryCandidate, ...]:
        """Idempotently stage previewed rows as pending candidates, never as memories."""
        staged: list[MemoryCandidate] = []
        for candidate in preview.candidates:
            existing = capture.get_candidate(candidate.candidate_id)
            if existing is not None:
                same_source = (
                    existing.content == candidate.content
                    and existing.category == candidate.category
                    and existing.kind is candidate.kind
                    and existing.source_refs == candidate.source_refs
                )
                if not same_source:
                    raise ValueError("legacy import candidate ID collision with different source content")
                staged.append(existing)
                continue
            staged.append(capture.capture(candidate))
        return tuple(staged)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
