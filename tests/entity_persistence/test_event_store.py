from datetime import datetime, timezone

import pytest

from sylphy_entity.contracts.events import EntityEvent
from sylphy_entity.contracts.observation import Observation
from sylphy_entity.persistence.event_store import EventStore


def test_event_append_is_durable_idempotent_and_ordered(entity_db):
    store = EventStore(entity_db)
    event = EntityEvent(event_type="test.signal", source="pytest", payload={"x": 1})
    first = store.append(event)
    second = store.append(event)
    assert first.inserted is True
    assert second.inserted is False
    assert first.sequence == second.sequence
    stored = store.read_after(0)
    assert len(stored) == 1
    assert stored[0].event.event_id == event.event_id
    assert stored[0].event.payload == {"x": 1}


def test_event_id_cannot_be_reused_with_different_content(entity_db):
    store = EventStore(entity_db)
    event = EntityEvent(event_type="test.signal", source="pytest", payload={"x": 1})
    store.append(event)
    altered = EntityEvent(
        event_id=event.event_id, event_type="test.signal", source="pytest",
        occurred_at=event.occurred_at, payload={"x": 2},
    )
    with pytest.raises(ValueError, match="reused"):
        store.append(altered)


def test_observation_is_stored_with_event_provenance(entity_db):
    store = EventStore(entity_db)
    observed = datetime.now(timezone.utc)
    observation = Observation(
        source_id="filesystem:sandbox", subject="file:note.txt",
        kind="filesystem.file_created", payload={"relative_path": "note.txt"},
        observed_at=observed, received_at=observed, permission_scope="sandbox:/tmp/test",
    )
    event = EntityEvent(
        event_type="world.observation", source=observation.source_id,
        payload={"observation": observation.model_dump(mode="json")},
    )
    appended = store.append(event)
    store.append_observation(observation, appended.sequence)
    with entity_db.read() as db:
        row = db.execute(
            "SELECT event_sequence,subject,kind FROM observations WHERE observation_id=?",
            (observation.observation_id,),
        ).fetchone()
    assert tuple(row) == (appended.sequence, "file:note.txt", "filesystem.file_created")


def test_stage1_runtime_database_is_adopted_without_losing_boot_history(tmp_path):
    import sqlite3

    from sylphy_entity.persistence.database import EntityDatabase
    from sylphy_entity.persistence.runtime_store import RuntimeStore

    path = tmp_path / "stage1-runtime.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE daemon_runtime (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            instance_id TEXT NOT NULL, boot_count INTEGER NOT NULL,
            started_at TEXT NOT NULL, last_shutdown_at TEXT,
            clean_shutdown INTEGER NOT NULL CHECK(clean_shutdown IN (0,1))
        )"""
    )
    connection.execute(
        "INSERT INTO daemon_runtime VALUES(1,'stage1-instance',4,'2026-01-01T00:00:00+00:00','2026-01-01T01:00:00+00:00',1)"
    )
    connection.commit()
    connection.close()

    database = EntityDatabase(path)
    database.open()
    runtime = RuntimeStore(database=database)
    try:
        boot = runtime.begin_runtime()
        assert boot.boot_count == 5
        assert boot.previous_shutdown_clean is True
        with database.read() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 5
            assert db.execute("SELECT boot_count FROM daemon_runtime WHERE singleton=1").fetchone()[0] == 5
            assert db.execute("SELECT value FROM entity_metadata WHERE key='entity_id'").fetchone()[0]
            assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entity_events'").fetchone()
    finally:
        runtime.close()
        database.close()


def test_schema_v1_migration_adds_pending_observation_projection_marker(tmp_path):
    import sqlite3

    from sylphy_entity.persistence.database import EntityDatabase

    path = tmp_path / "schema-v1.sqlite3"
    database = EntityDatabase(path)
    database.open()
    database.close()
    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE observations DROP COLUMN projected")
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()

    migrated = EntityDatabase(path)
    migrated.open()
    try:
        with migrated.read() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            columns = {row[1] for row in db.execute("PRAGMA table_info(observations)")}
            assert version == 5
            assert "projected" in columns
            assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    finally:
        migrated.close()


def test_observation_id_cannot_be_reused_with_different_content(entity_db):
    store = EventStore(entity_db)
    first = Observation(
        observation_id="stable-observation-id", source_id="filesystem:sandbox",
        subject="file:one.txt", kind="filesystem.file_created",
        payload={"relative_path": "one.txt"}, permission_scope="sandbox:/allowed",
    )
    first_event = EntityEvent(
        event_type="world.observation", source=first.source_id,
        payload={"observation": first.model_dump(mode="json")},
    )
    first_seq = store.append(first_event).sequence
    assert store.append_observation(first, first_seq) is True

    altered = Observation(
        observation_id=first.observation_id, source_id=first.source_id,
        subject="file:two.txt", kind=first.kind,
        payload={"relative_path": "two.txt"}, permission_scope="sandbox:/allowed",
    )
    altered_event = EntityEvent(
        event_type="world.observation", source=altered.source_id,
        payload={"observation": altered.model_dump(mode="json")},
    )
    altered_seq = store.append(altered_event).sequence
    with pytest.raises(ValueError, match="observation_id was reused"):
        store.append_observation(altered, altered_seq)


def test_identical_observation_redelivery_keeps_original_event_provenance(entity_db):
    store = EventStore(entity_db)
    observation = Observation(
        observation_id="redelivered-observation", source_id="filesystem:sandbox",
        subject="file:repeat.txt", kind="filesystem.file_created",
        payload={"relative_path": "repeat.txt"}, permission_scope="sandbox:/allowed",
    )
    event_a = EntityEvent(
        event_type="world.observation", source=observation.source_id,
        payload={"observation": observation.model_dump(mode="json")},
    )
    event_b = EntityEvent(
        event_type="world.observation", source=observation.source_id,
        payload={"observation": observation.model_dump(mode="json")},
    )
    seq_a = store.append(event_a).sequence
    seq_b = store.append(event_b).sequence
    assert seq_b > seq_a
    assert store.append_observation(observation, seq_a) is True
    assert store.append_observation(observation, seq_b) is True
    with entity_db.read() as db:
        row = db.execute(
            "SELECT event_sequence,projected FROM observations WHERE observation_id=?",
            (observation.observation_id,),
        ).fetchone()
    assert row[0] == seq_a
    assert row[1] == 0
