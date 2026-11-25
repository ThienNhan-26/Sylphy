"""Single-writer SQLite database for durable SYLPHY Entity state (Stage 5)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Iterator


_SCHEMA_VERSION = 5


class EntityDatabase:
    """Own one SQLite connection and serialize every transaction through it.

    The daemon is a modular monolith, so persistence deliberately uses one
    connection/writer path rather than introducing a broker or DB service.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = RLock()

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    def open(self) -> None:
        with self._lock:
            if self._connection is not None:
                raise RuntimeError("EntityDatabase is already open")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path, timeout=10.0, isolation_level=None, check_same_thread=False
            )
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=10000")
                self._connection = connection
                self._migrate()
            except Exception:
                self._connection = None
                connection.close()
                raise

    def _migrate(self) -> None:
        connection = self.connection
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > _SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema {version} is newer than supported {_SCHEMA_VERSION}"
            )
        if version == 0:
            with self._lock:
                connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS entity_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS entity_events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL UNIQUE,
                        event_type TEXT NOT NULL,
                        source TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        recorded_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        correlation_id TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_entity_events_type_sequence
                        ON entity_events(event_type, sequence);

                    CREATE TABLE IF NOT EXISTS observations (
                        observation_id TEXT PRIMARY KEY,
                        event_sequence INTEGER NOT NULL REFERENCES entity_events(sequence),
                        source_id TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        received_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        projected INTEGER NOT NULL DEFAULT 0 CHECK (projected IN (0, 1))
                    );
                    CREATE INDEX IF NOT EXISTS idx_observations_subject
                        ON observations(subject, received_at);

                    CREATE TABLE IF NOT EXISTS world_snapshots (
                        snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        world_version INTEGER NOT NULL,
                        event_sequence INTEGER NOT NULL DEFAULT 0,
                        captured_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        checksum_sha256 TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_world_snapshots_latest
                        ON world_snapshots(snapshot_id DESC);

                    CREATE TABLE IF NOT EXISTS entity_state_snapshots (
                        revision INTEGER PRIMARY KEY AUTOINCREMENT,
                        entity_id TEXT NOT NULL,
                        schema_version INTEGER NOT NULL,
                        saved_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        checksum_sha256 TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_entity_state_latest
                        ON entity_state_snapshots(revision DESC);

                    CREATE TABLE IF NOT EXISTS goals (
                        goal_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        source TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);

                    CREATE TABLE IF NOT EXISTS intents (
                        intent_id TEXT PRIMARY KEY,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        goal_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        permission_scope TEXT NOT NULL,
                        fingerprint_sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        error TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status);

                    CREATE TABLE IF NOT EXISTS resource_leases (
                        lease_id TEXT PRIMARY KEY,
                        intent_id TEXT NOT NULL,
                        resource_key TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        acquired_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        released_at TEXT,
                        state TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_resource_leases_state
                        ON resource_leases(state, expires_at);

                    CREATE TABLE IF NOT EXISTS executions (
                        execution_id TEXT PRIMARY KEY,
                        intent_id TEXT NOT NULL REFERENCES intents(intent_id),
                        attempt INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        worker_pid INTEGER,
                        worker_create_time REAL,
                        worker_return_code INTEGER,
                        actual_result_json TEXT NOT NULL DEFAULT '{}',
                        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                        error TEXT,
                        UNIQUE(intent_id, attempt)
                    );
                    CREATE INDEX IF NOT EXISTS idx_executions_status
                        ON executions(status, started_at);

                    CREATE TABLE IF NOT EXISTS worker_processes (
                        worker_id TEXT PRIMARY KEY,
                        intent_id TEXT NOT NULL,
                        pid INTEGER NOT NULL,
                        create_time REAL,
                        daemon_instance_id TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        state TEXT NOT NULL,
                        return_code INTEGER,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE INDEX IF NOT EXISTS idx_worker_processes_state
                        ON worker_processes(state);

                    CREATE TABLE IF NOT EXISTS evidence (
                        evidence_id TEXT PRIMARY KEY,
                        intent_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        sha256 TEXT,
                        verified_at TEXT NOT NULL,
                        metadata_json TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS permissions (
                        permission_id TEXT PRIMARY KEY,
                        permission_class TEXT NOT NULL,
                        permission_scope TEXT NOT NULL,
                        state TEXT NOT NULL,
                        granted_at TEXT NOT NULL,
                        revoked_at TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    );

                    CREATE TABLE IF NOT EXISTS recovery_runs (
                        recovery_id TEXT PRIMARY KEY,
                        started_at TEXT NOT NULL,
                        finished_at TEXT NOT NULL,
                        previous_shutdown_clean INTEGER,
                        report_json TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS entity_memories (
                        memory_id TEXT PRIMARY KEY,
                        content TEXT NOT NULL CHECK(length(trim(content)) > 0),
                        category TEXT NOT NULL CHECK(length(trim(category)) BETWEEN 1 AND 80),
                        origin TEXT NOT NULL CHECK(origin IN ('user_confirmed','cognition_confirmed','imported_verified')),
                        source_refs_json TEXT NOT NULL,
                        confidence REAL CHECK(confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
                        status TEXT NOT NULL CHECK(status IN ('active','archived')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_entity_memories_status_created
                        ON entity_memories(status,created_at,memory_id);
                    PRAGMA user_version=3;
                    COMMIT;
                    """
                )
                version = 3
        elif version == 1:
            with self._lock:
                connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    ALTER TABLE observations ADD COLUMN projected INTEGER NOT NULL DEFAULT 0
                        CHECK (projected IN (0, 1));
                    PRAGMA user_version=2;
                    COMMIT;
                    """
                )
                version = 2
        if version == 2:
            with self._lock:
                connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS entity_memories (
                        memory_id TEXT PRIMARY KEY,
                        content TEXT NOT NULL CHECK(length(trim(content)) > 0),
                        category TEXT NOT NULL CHECK(length(trim(category)) BETWEEN 1 AND 80),
                        origin TEXT NOT NULL CHECK(origin IN ('user_confirmed','cognition_confirmed','imported_verified')),
                        source_refs_json TEXT NOT NULL,
                        confidence REAL CHECK(confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
                        status TEXT NOT NULL CHECK(status IN ('active','archived')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_entity_memories_status_created
                        ON entity_memories(status,created_at,memory_id);
                    PRAGMA user_version=3;
                    COMMIT;
                    """
                )
                version = 3
        if version == 3:
            with self._lock:
                connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS entity_memory_candidates (
                        candidate_id TEXT PRIMARY KEY,
                        content TEXT NOT NULL CHECK(length(trim(content)) BETWEEN 1 AND 8000),
                        category TEXT NOT NULL CHECK(length(trim(category)) BETWEEN 1 AND 80),
                        kind TEXT NOT NULL CHECK(kind IN ('user_statement','cognition_proposal','external_import')),
                        source_refs_json TEXT NOT NULL,
                        confidence REAL CHECK(confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
                        status TEXT NOT NULL CHECK(status IN ('pending','confirmed','rejected')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        decision_ref TEXT,
                        resulting_memory_id TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_entity_memory_candidates_status_created
                        ON entity_memory_candidates(status,created_at,candidate_id);
                    PRAGMA user_version=4;
                    COMMIT;
                    """
                )
                version = 4
        if version == 4:
            with self._lock:
                connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS entity_memory_revisions (
                        revision_id TEXT PRIMARY KEY,
                        memory_id TEXT NOT NULL REFERENCES entity_memories(memory_id) ON DELETE CASCADE,
                        action TEXT NOT NULL CHECK(action IN ('corrected','archived','restored')),
                        content TEXT NOT NULL,
                        category TEXT NOT NULL,
                        origin TEXT NOT NULL CHECK(origin IN ('user_confirmed','cognition_confirmed','imported_verified')),
                        source_refs_json TEXT NOT NULL,
                        confidence REAL CHECK(confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
                        status TEXT NOT NULL CHECK(status IN ('active','archived')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        changed_at TEXT NOT NULL,
                        change_refs_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_entity_memory_revisions_parent_time
                        ON entity_memory_revisions(memory_id, changed_at, revision_id);
                    PRAGMA user_version=5;
                    COMMIT;
                    """
                )
                version = 5

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("EntityDatabase is not open")
        return self._connection

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self.connection
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self.connection

    def close(self) -> None:
        with self._lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                connection.close()
