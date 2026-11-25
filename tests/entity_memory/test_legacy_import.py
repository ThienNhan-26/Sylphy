import sqlite3

from sylphy_entity.contracts.memory import MemoryCandidateKind, MemoryCandidateStatus
from sylphy_entity.memory.legacy_import import LegacyMemoryImporter
from sylphy_entity.memory.repository import MemoryCaptureService, MemoryRepository


def _legacy_db(path):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE memories (
        id TEXT PRIMARY KEY, content TEXT NOT NULL, category TEXT NOT NULL,
        created_at TEXT, updated_at TEXT, source_session_id TEXT,
        source_message_id TEXT, confidence REAL, status TEXT NOT NULL,
        origin TEXT, source_message_ids TEXT
    )""")
    db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
        "m-1", "Prefers local-first tools", "preference", "2026-01-01T00:00:00+00:00",
        "2026-01-02T00:00:00+00:00", "session-1", "message-1", 0.9,
        "confirmed", "manual", '["message-1","message-2"]'))
    db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
        "m-pending", "Unconfirmed proposal", "other", None, None, None, None,
        None, "pending", "unknown", '[]'))
    db.commit(); db.close()


def test_preview_reads_only_confirmed_allowlisted_rows_and_preserves_provenance(tmp_path):
    path = tmp_path / "legacy.sqlite3"; _legacy_db(path)
    preview = LegacyMemoryImporter().preview(path)
    assert len(preview.candidates) == 1
    candidate = preview.candidates[0]
    assert candidate.kind is MemoryCandidateKind.EXTERNAL_IMPORT
    assert candidate.status is MemoryCandidateStatus.PENDING
    assert "legacy-memory:m-1" in candidate.source_refs
    assert "legacy-message:message-2" in candidate.source_refs
    assert preview.skipped == ()


def test_stage_creates_pending_candidates_not_active_memories(tmp_path, entity_db):
    path = tmp_path / "legacy.sqlite3"; _legacy_db(path)
    importer = LegacyMemoryImporter()
    preview = importer.preview(path)
    capture = MemoryCaptureService(entity_db)
    staged = importer.stage(preview, capture)
    assert len(staged) == 1
    assert staged[0].status is MemoryCandidateStatus.PENDING
    assert MemoryRepository(entity_db).list_active() == ()
    assert capture.get_candidate(staged[0].candidate_id) == staged[0]
    assert importer.stage(preview, capture) == staged


def test_preview_is_read_only_and_rejects_missing_memory_table(tmp_path):
    path = tmp_path / "not-legacy.sqlite3"
    sqlite3.connect(path).close()
    try:
        LegacyMemoryImporter().preview(path)
    except ValueError as exc:
        assert "memories table" in str(exc)
    else:
        raise AssertionError("expected a schema rejection")


def test_preview_enforces_limit(tmp_path):
    path = tmp_path / "legacy.sqlite3"; _legacy_db(path)
    try:
        LegacyMemoryImporter().preview(path, limit=0)
    except ValueError as exc:
        assert "limit" in str(exc)
    else:
        raise AssertionError("expected limit validation")


def test_restage_after_candidate_decision_returns_terminal_candidate(tmp_path, entity_db):
    path = tmp_path / "legacy.sqlite3"; _legacy_db(path)
    importer = LegacyMemoryImporter()
    preview = importer.preview(path)
    capture = MemoryCaptureService(entity_db)
    staged = importer.stage(preview, capture)
    rejected = capture.reject(staged[0].candidate_id, decision_ref="user:reject-import")
    assert importer.stage(preview, capture) == (rejected,)
