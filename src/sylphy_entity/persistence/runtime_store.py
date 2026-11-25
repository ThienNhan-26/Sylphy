"""Daemon boot metadata stored in the Entity's shared SQLite database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .database import EntityDatabase


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class RuntimeBootInfo:
    instance_id: str
    boot_count: int
    previous_shutdown_clean: bool | None
    previous_shutdown_at: str | None


class RuntimeStore:
    def __init__(self, path: Path | None = None, *, database: EntityDatabase | None = None) -> None:
        if (path is None) == (database is None):
            raise ValueError("provide exactly one of path or database")
        self._database = database or EntityDatabase(Path(path))
        self._owns_database = database is None
        self._boot_info: RuntimeBootInfo | None = None

    @property
    def boot_info(self) -> RuntimeBootInfo:
        if self._boot_info is None:
            raise RuntimeError("RuntimeStore has not been started")
        return self._boot_info

    @property
    def database(self) -> EntityDatabase:
        return self._database

    def begin_runtime(self) -> RuntimeBootInfo:
        if self._boot_info is not None:
            raise RuntimeError("RuntimeStore is already started")
        if not self._database.is_open:
            self._database.open()
        now = _utc_now()
        with self._database.transaction() as db:
            row = db.execute(
                "SELECT boot_count, last_shutdown_at, clean_shutdown "
                "FROM daemon_runtime WHERE singleton = 1"
            ).fetchone() if self._table_exists(db, "daemon_runtime") else None
            if row is None:
                previous_clean = None
                previous_shutdown_at = None
                boot_count = 1
                db.execute(
                    """CREATE TABLE IF NOT EXISTS daemon_runtime (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        instance_id TEXT NOT NULL,
                        boot_count INTEGER NOT NULL,
                        started_at TEXT NOT NULL,
                        last_shutdown_at TEXT,
                        clean_shutdown INTEGER NOT NULL CHECK (clean_shutdown IN (0, 1))
                    )"""
                )
            else:
                boot_count = int(row[0]) + 1
                previous_shutdown_at = row[1]
                previous_clean = bool(row[2])
            instance_id = str(uuid4())
            db.execute(
                """INSERT INTO daemon_runtime (
                    singleton, instance_id, boot_count, started_at,
                    last_shutdown_at, clean_shutdown
                ) VALUES (1, ?, ?, ?, ?, 0)
                ON CONFLICT(singleton) DO UPDATE SET
                    instance_id=excluded.instance_id,
                    boot_count=excluded.boot_count,
                    started_at=excluded.started_at,
                    clean_shutdown=0""",
                (instance_id, boot_count, now, previous_shutdown_at),
            )
            db.execute(
                "INSERT INTO entity_metadata(key, value, updated_at) VALUES('entity_id', ?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                (str(uuid4()), now),
            )
        self._boot_info = RuntimeBootInfo(
            instance_id=instance_id,
            boot_count=boot_count,
            previous_shutdown_clean=previous_clean,
            previous_shutdown_at=previous_shutdown_at,
        )
        return self._boot_info

    @staticmethod
    def _table_exists(db, name: str) -> bool:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def mark_clean_shutdown(self) -> None:
        if self._boot_info is None:
            raise RuntimeError("RuntimeStore is not open")
        with self._database.transaction() as db:
            db.execute(
                "UPDATE daemon_runtime SET clean_shutdown=1, last_shutdown_at=? WHERE singleton=1",
                (_utc_now(),),
            )

    def close(self) -> None:
        self._boot_info = None
        if self._owns_database:
            self._database.close()
