from datetime import datetime, timezone
import sqlite3

import pytest

from sylphy_entity.contracts.memory import MemoryOrigin, MemoryRecord, MemoryStatus
from sylphy_entity.memory import MemoryRepository
from sylphy_entity.persistence.database import EntityDatabase


def make_memory(**overrides):
    values = {
        "content": "The user prefers local-first tools.",
        "category": "preference",
        "origin": MemoryOrigin.USER_CONFIRMED,
        "source_refs": ("event:user-confirmed-1",),
    }
    values.update(overrides)
    return MemoryRecord(**values)


def test_create_get_and_restart_preserve_provenance(tmp_path):
    path = tmp_path / "entity.sqlite3"
    db = EntityDatabase(path)
    db.open()
    repository = MemoryRepository(db)
    memory = make_memory()
    assert repository.create(memory) == memory
    assert repository.get(memory.memory_id) == memory
    db.close()

    reopened = EntityDatabase(path)
    reopened.open()
    try:
        assert MemoryRepository(reopened).get(memory.memory_id) == memory
    finally:
        reopened.close()


def test_create_is_idempotent_for_same_id_and_rejects_changed_content(entity_db):
    repository = MemoryRepository(entity_db)
    memory = make_memory()
    assert repository.create(memory) == memory
    assert repository.create(memory) == memory
    changed = make_memory(memory_id=memory.memory_id, content="Different fact")
    with pytest.raises(ValueError, match="reused"):
        repository.create(changed)


def test_memory_requires_explicit_provenance():
    with pytest.raises(ValueError, match="source_ref"):
        make_memory(source_refs=())


def test_memory_validates_confidence_and_timestamp():
    with pytest.raises(ValueError, match="confidence"):
        make_memory(confidence=1.1)
    naive = datetime(2026, 1, 1)
    with pytest.raises(ValueError, match="timezone-aware"):
        make_memory(created_at=naive)


def test_archive_is_idempotent_and_active_listing_is_bounded(entity_db):
    repository = MemoryRepository(entity_db)
    first = make_memory()
    second = make_memory(content="A second explicit preference.")
    repository.create(first)
    repository.create(second)
    assert len(repository.list_active(limit=1)) == 1
    assert repository.archive(first.memory_id, change_ref="user:archive-first") is True
    assert repository.archive(first.memory_id, change_ref="user:archive-first-again") is False
    assert repository.get(first.memory_id).status is MemoryStatus.ARCHIVED
    assert [item.memory_id for item in repository.list_active()] == [second.memory_id]


def test_repository_rejects_unbounded_list_limit(entity_db):
    with pytest.raises(ValueError, match="limit"):
        MemoryRepository(entity_db).list_active(limit=501)


def test_v2_database_migrates_additively_to_memory_schema(tmp_path):
    path = tmp_path / "legacy-v2.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    connection.close()

    db = EntityDatabase(path)
    db.open()
    try:
        with db.read() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='entity_memories'"
            ).fetchone()
        assert version == 5
        assert table is not None
    finally:
        db.close()


def test_v4_database_migrates_additively_to_revision_history(tmp_path):
    path = tmp_path / "entity-v4.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=4")
    connection.commit(); connection.close()
    db = EntityDatabase(path); db.open()
    try:
        with db.read() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='entity_memory_revisions'"
            ).fetchone()
        assert version == 5
        assert table is not None
    finally:
        db.close()


def test_v4_migration_preserves_existing_memory_rows(tmp_path):
    path = tmp_path / "entity-v4-with-memory.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE entity_memories (
            memory_id TEXT PRIMARY KEY, content TEXT NOT NULL, category TEXT NOT NULL,
            origin TEXT NOT NULL, source_refs_json TEXT NOT NULL, confidence REAL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO entity_memories VALUES (
            'keep-me','Existing memory','preference','user_confirmed','[\"event:old\"]',
            0.8,'active','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00'
        );
        PRAGMA user_version=4;
    """)
    connection.commit(); connection.close()
    db = EntityDatabase(path); db.open()
    try:
        memory = MemoryRepository(db).get("keep-me")
        assert memory is not None
        assert memory.content == "Existing memory"
        assert memory.source_refs == ("event:old",)
        with db.read() as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    finally:
        db.close()
