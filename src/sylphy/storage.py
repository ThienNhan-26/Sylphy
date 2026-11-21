"""SQLite-backed durable sessions and messages."""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import re
import hashlib

from .core import Message, Session
from .memory import Memory
from .cognition import CognitionTask, MemoryCandidate
from .exploration import ExplorationQuestion, ExplorationTask
from .investigation import (
    Investigation, InvestigationBudget, InvestigationBudgetError, InvestigationEvidence,
    InvestigationFinding, InvestigationResult, InvestigationStatus,
)


class SQLiteStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with self._connect() as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, title TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL, created_at TEXT NOT NULL, completed INTEGER NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")
            db.execute("""CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, content TEXT NOT NULL, category TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                source_session_id TEXT, source_message_id TEXT,
                confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
                status TEXT NOT NULL DEFAULT 'confirmed'
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_memories_status_updated ON memories(status, updated_at DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_memories_source_session ON memories(source_session_id)")
            self._ensure_candidate_schema(db)
            db.execute("""CREATE TABLE IF NOT EXISTS cognition_tasks (
                id TEXT PRIMARY KEY, source_session_id TEXT NOT NULL, source_message_ids TEXT NOT NULL,
                created_at TEXT NOT NULL, status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT, started_at TEXT, completed_at TEXT,
                UNIQUE(source_session_id, source_message_ids)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_cognition_tasks_status_created ON cognition_tasks(status, created_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS exploration_tasks (
                id TEXT PRIMARY KEY, source_session_id TEXT NOT NULL, source_message_ids TEXT NOT NULL,
                created_at TEXT NOT NULL, status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT, started_at TEXT, completed_at TEXT,
                UNIQUE(source_session_id, source_message_ids)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_exploration_tasks_status_created ON exploration_tasks(status, created_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS exploration_attempts (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, attempted_at TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_exploration_attempts_time ON exploration_attempts(attempted_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS exploration_questions (
                id TEXT PRIMARY KEY, question TEXT NOT NULL, source_session_id TEXT NOT NULL,
                source_message_ids TEXT NOT NULL, source_memory_ids TEXT NOT NULL,
                created_at TEXT NOT NULL, status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
                expires_at TEXT NOT NULL, topic TEXT, reason TEXT, question_key TEXT NOT NULL UNIQUE,
                notification_status TEXT CHECK(notification_status IS NULL OR
                    notification_status IN ('queued','delivered','dismissed','expired'))
            )""")
            self._ensure_exploration_notification_schema(db)
            db.execute("CREATE INDEX IF NOT EXISTS idx_exploration_questions_status_expiry ON exploration_questions(status, expires_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS investigations (
                id TEXT PRIMARY KEY,
                source_question_id TEXT NOT NULL UNIQUE REFERENCES exploration_questions(id) ON DELETE RESTRICT,
                question TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled','expired')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
                max_attempts INTEGER NOT NULL CHECK(max_attempts >= 1),
                last_error TEXT, deferred_at TEXT,
                max_duration_seconds INTEGER NOT NULL CHECK(max_duration_seconds > 0),
                max_model_calls INTEGER NOT NULL CHECK(max_model_calls > 0),
                max_search_requests INTEGER NOT NULL CHECK(max_search_requests > 0),
                max_pages INTEGER NOT NULL CHECK(max_pages > 0),
                max_source_bytes INTEGER NOT NULL CHECK(max_source_bytes > 0),
                max_extracted_text_chars INTEGER NOT NULL CHECK(max_extracted_text_chars > 0),
                started_at TEXT,
                search_calls_used INTEGER NOT NULL DEFAULT 0 CHECK(search_calls_used >= 0),
                pages_read INTEGER NOT NULL DEFAULT 0 CHECK(pages_read >= 0),
                model_calls_used INTEGER NOT NULL DEFAULT 0 CHECK(model_calls_used >= 0),
                source_bytes_used INTEGER NOT NULL DEFAULT 0 CHECK(source_bytes_used >= 0),
                extracted_chars_used INTEGER NOT NULL DEFAULT 0 CHECK(extracted_chars_used >= 0)
            )""")
            self._ensure_investigation_execution_schema(db)
            db.execute("CREATE INDEX IF NOT EXISTS idx_investigations_status_created ON investigations(status, created_at, id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_investigations_expiry ON investigations(status, expires_at)")
            db.execute("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            # Preserve G0 turns when opening its existing database.
            columns = {row[1] for row in db.execute("PRAGMA table_info(turns)")} if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turns'"
            ).fetchone() else set()
            migrated = db.execute("SELECT value FROM schema_meta WHERE key='g0_turns_migrated'").fetchone()
            if columns and not migrated:
                for conversation_id, created_at in db.execute("SELECT DISTINCT conversation_id, MIN(created_at) FROM turns GROUP BY conversation_id"):
                    db.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?, ?, NULL)", (conversation_id, created_at, created_at))
                    for row in db.execute("SELECT user_text, assistant_text, created_at FROM turns WHERE conversation_id=? ORDER BY id", (conversation_id,)):
                        stamp = row[2]
                        db.execute("INSERT OR IGNORE INTO messages VALUES (?, ?, 'user', ?, ?, 1)", (str(uuid4()), conversation_id, row[0], stamp))
                        db.execute("INSERT OR IGNORE INTO messages VALUES (?, ?, 'assistant', ?, ?, 1)", (str(uuid4()), conversation_id, row[1], stamp))
                db.execute("INSERT INTO schema_meta(key, value) VALUES ('g0_turns_migrated', '1')")

    def _ensure_investigation_execution_schema(self, db: sqlite3.Connection) -> None:
        """Add execution counters and result tables without rebuilding existing data."""
        db.execute("SAVEPOINT investigation_execution_schema")
        try:
            self._apply_investigation_execution_schema(db)
        except Exception:
            db.execute("ROLLBACK TO investigation_execution_schema")
            db.execute("RELEASE investigation_execution_schema")
            raise
        else:
            db.execute("RELEASE investigation_execution_schema")

    @staticmethod
    def _apply_investigation_execution_schema(db: sqlite3.Connection) -> None:
        columns = {row[1] for row in db.execute("PRAGMA table_info(investigations)")}
        additions = {
            "started_at": "TEXT",
            "search_calls_used": "INTEGER NOT NULL DEFAULT 0 CHECK(search_calls_used >= 0)",
            "pages_read": "INTEGER NOT NULL DEFAULT 0 CHECK(pages_read >= 0)",
            "model_calls_used": "INTEGER NOT NULL DEFAULT 0 CHECK(model_calls_used >= 0)",
            "source_bytes_used": "INTEGER NOT NULL DEFAULT 0 CHECK(source_bytes_used >= 0)",
            "extracted_chars_used": "INTEGER NOT NULL DEFAULT 0 CHECK(extracted_chars_used >= 0)",
        }
        for name, definition in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE investigations ADD COLUMN {name} {definition}")
        db.execute("""CREATE TABLE IF NOT EXISTS investigation_evidence (
            id TEXT PRIMARY KEY,
            investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            source_url TEXT NOT NULL, provider TEXT NOT NULL, search_rank INTEGER NOT NULL,
            title TEXT NOT NULL, snippet TEXT NOT NULL, status TEXT NOT NULL,
            selected_at TEXT NOT NULL, retrieved_at TEXT, final_url TEXT, content_type TEXT,
            text TEXT NOT NULL DEFAULT '', content_hash TEXT, source_bytes INTEGER NOT NULL DEFAULT 0,
            error TEXT, UNIQUE(investigation_id, source_url)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_investigation_evidence_parent ON investigation_evidence(investigation_id, search_rank, id)")
        db.execute("""CREATE TABLE IF NOT EXISTS investigation_results (
            investigation_id TEXT PRIMARY KEY REFERENCES investigations(id) ON DELETE CASCADE,
            summary TEXT NOT NULL, findings_json TEXT NOT NULL, uncertainty TEXT NOT NULL,
            limitations_json TEXT NOT NULL, evidence_sufficient INTEGER NOT NULL CHECK(evidence_sufficient IN (0,1)),
            created_at TEXT NOT NULL
        )""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    @staticmethod
    def _ensure_exploration_notification_schema(db: sqlite3.Connection) -> None:
        columns = {row[1] for row in db.execute("PRAGMA table_info(exploration_questions)")}
        if "notification_status" not in columns:
            db.execute("""ALTER TABLE exploration_questions ADD COLUMN notification_status TEXT
                CHECK(notification_status IS NULL OR
                    notification_status IN ('queued','delivered','dismissed','expired'))""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_exploration_notification_status
            ON exploration_questions(notification_status, status, expires_at)""")

    def _ensure_candidate_schema(self, db: sqlite3.Connection) -> None:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_candidates'").fetchone()
        if not exists:
            db.execute("""CREATE TABLE memory_candidates (
                id TEXT PRIMARY KEY, content TEXT NOT NULL, category TEXT NOT NULL,
                source_session_id TEXT NOT NULL, source_message_ids TEXT NOT NULL,
                created_at TEXT NOT NULL, status TEXT NOT NULL,
                confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
                candidate_key TEXT NOT NULL UNIQUE
            )""")
            db.execute("CREATE INDEX idx_candidates_status_created ON memory_candidates(status, created_at)")
            return

        columns = {row[1] for row in db.execute("PRAGMA table_info(memory_candidates)")}
        unique_source_turn = False
        for index in db.execute("PRAGMA index_list(memory_candidates)").fetchall():
            if index[2]:
                names = [row[2] for row in db.execute(f"PRAGMA index_info('{index[1]}')")]
                if names == ["source_session_id", "source_message_ids"]:
                    unique_source_turn = True
        if "candidate_key" in columns and not unique_source_turn:
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_identity ON memory_candidates(candidate_key)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_candidates_status_created ON memory_candidates(status, created_at)")
            return

        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("""CREATE TABLE memory_candidates_replacement (
                id TEXT PRIMARY KEY, content TEXT NOT NULL, category TEXT NOT NULL,
                source_session_id TEXT NOT NULL, source_message_ids TEXT NOT NULL,
                created_at TEXT NOT NULL, status TEXT NOT NULL,
                confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
                candidate_key TEXT NOT NULL UNIQUE
            )""")
            old_rows = db.execute("SELECT * FROM memory_candidates").fetchall()
            old_columns = [row[1] for row in db.execute("PRAGMA table_info(memory_candidates)")]
            for row in old_rows:
                values = dict(zip(old_columns, row))
                key = values.get("candidate_key") or self._candidate_identity(
                    values["source_session_id"], values["source_message_ids"], values["content"], values["category"])
                db.execute("""INSERT INTO memory_candidates_replacement
                    (id, content, category, source_session_id, source_message_ids, created_at, status, confidence, candidate_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (values["id"], values["content"], values["category"], values["source_session_id"],
                     values["source_message_ids"], values["created_at"], values["status"],
                     values.get("confidence"), key))
            db.execute("DROP TABLE memory_candidates")
            db.execute("ALTER TABLE memory_candidates_replacement RENAME TO memory_candidates")
            db.execute("CREATE UNIQUE INDEX idx_candidates_identity ON memory_candidates(candidate_key)")
            db.execute("CREATE INDEX idx_candidates_status_created ON memory_candidates(status, created_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
            db.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('candidate_identity_version', '1')")
            db.commit()
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def _session(row) -> Session:
        return Session(row[0], datetime.fromisoformat(row[1]), datetime.fromisoformat(row[2]), row[3])

    def create_session(self, title: str | None = None) -> Session:
        now = datetime.now(timezone.utc)
        session = Session(str(uuid4()), now, now, title.strip() if title and title.strip() else None)
        with self._connect() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (session.id, now.isoformat(), now.isoformat(), session.title))
        return session

    def list_sessions(self) -> list[Session]:
        with self._connect() as db:
            rows = db.execute("SELECT id, created_at, updated_at, title FROM sessions ORDER BY updated_at DESC, id").fetchall()
        return [self._session(row) for row in rows]

    def get_session(self, session_id: str) -> Session | None:
        with self._connect() as db:
            row = db.execute("SELECT id, created_at, updated_at, title FROM sessions WHERE id=?", (session_id,)).fetchone()
        return self._session(row) if row else None

    def rename_session(self, session_id: str, title: str | None) -> Session:
        clean_title = title.strip() if title and title.strip() else None
        with self._connect() as db:
            updated = db.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (clean_title, datetime.now(timezone.utc).isoformat(), session_id))
            if updated.rowcount == 0:
                raise KeyError(f"Session not found: {session_id}")
        return self.get_session(session_id)  # type: ignore[return-value]

    def delete_session(self, session_id: str) -> None:
        with self._connect() as db:
            deleted = db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            if deleted.rowcount == 0:
                raise KeyError(f"Session not found: {session_id}")

    def messages(self, session_id: str) -> list[Message]:
        with self._connect() as db:
            rows = db.execute("SELECT id, session_id, role, content, created_at, completed FROM messages WHERE session_id=? ORDER BY rowid", (session_id,)).fetchall()
        return [Message(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), bool(row[5])) for row in rows]

    def append_turn(self, user: Message, assistant: Message) -> None:
        if user.session_id != assistant.session_id or user.role != "user" or assistant.role != "assistant":
            raise ValueError("Turn messages must be a user/assistant pair for the same session")
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM sessions WHERE id=?", (user.session_id,)).fetchone():
                raise KeyError(f"Session not found: {user.session_id}")
            db.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", [
                (user.id, user.session_id, user.role, user.content, user.created_at.isoformat(), int(user.completed)),
                (assistant.id, assistant.session_id, assistant.role, assistant.content, assistant.created_at.isoformat(), int(assistant.completed)),
            ])
            db.execute("UPDATE sessions SET updated_at=MAX(updated_at, ?) WHERE id=?", (assistant.created_at.isoformat(), user.session_id))

    @staticmethod
    def _memory(row) -> Memory:
        return Memory(row[0], row[1], row[2], datetime.fromisoformat(row[3]), datetime.fromisoformat(row[4]),
                      row[5], row[6], row[7], row[8])

    def create_memory(self, content: str, category: str, source_session_id: str | None = None,
                      source_message_id: str | None = None, confidence: float | None = None) -> Memory:
        now = datetime.now(timezone.utc)
        memory = Memory(str(uuid4()), content, category, now, now, source_session_id, source_message_id,
                        confidence, "confirmed")
        with self._connect() as db:
            db.execute("INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (memory.id, memory.content, memory.category, now.isoformat(), now.isoformat(),
                        source_session_id, source_message_id, confidence, memory.status))
        return memory

    def list_memories(self, status: str | None = "confirmed") -> list[Memory]:
        with self._connect() as db:
            if status is None:
                rows = db.execute("SELECT * FROM memories ORDER BY updated_at DESC, id").fetchall()
            else:
                rows = db.execute("SELECT * FROM memories WHERE status=? ORDER BY updated_at DESC, id", (status,)).fetchall()
        return [self._memory(row) for row in rows]

    def get_memory(self, memory_id: str) -> Memory | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return self._memory(row) if row else None

    def update_memory(self, memory_id: str, content: str, category: str | None = None,
                      confidence: float | None = None) -> Memory:
        with self._connect() as db:
            existing = db.execute("SELECT category, confidence FROM memories WHERE id=?", (memory_id,)).fetchone()
            if existing is None:
                raise KeyError(f"Memory not found: {memory_id}")
            db.execute("UPDATE memories SET content=?, category=?, confidence=?, updated_at=? WHERE id=?",
                       (content, category if category is not None else existing[0],
                        confidence if confidence is not None else existing[1],
                        datetime.now(timezone.utc).isoformat(), memory_id))
        return self.get_memory(memory_id)  # type: ignore[return-value]

    def delete_memory(self, memory_id: str) -> None:
        with self._connect() as db:
            deleted = db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            if deleted.rowcount == 0:
                raise KeyError(f"Memory not found: {memory_id}")

    @staticmethod
    def _search_tokens(text: str) -> list[str]:
        """Tokenize memory search text without substring false positives."""
        return [token.casefold() for token in re.findall(r"[\w]+", text, flags=re.UNICODE) if len(token) > 2]

    def search_memories(self, query: str, limit: int = 20) -> list[Memory]:
        if limit <= 0 or not query.strip():
            return []
        terms = list(dict.fromkeys(self._search_tokens(query)))
        if not terms:
            return []
        memories = self.list_memories("confirmed")
        ranked: list[tuple[int, Memory]] = []
        query_phrase = " ".join(terms)
        for memory in memories:
            content_tokens = self._search_tokens(memory.content)
            category_tokens = self._search_tokens(memory.category)
            content_counts = {term: content_tokens.count(term) for term in terms}
            category_counts = {term: category_tokens.count(term) for term in terms}
            # Exact token matches prevent matches such as "art" inside "partial".
            score = sum(count * 2 for count in content_counts.values())
            score += sum(count for count in category_counts.values())
            matched = sum(1 for term, count in content_counts.items() if count > 0)
            if matched == len(terms) and terms:
                score += 2  # Reward memories covering all query terms.
            normalized_content = " ".join(content_tokens)
            if len(terms) > 1 and query_phrase in normalized_content:
                score += 3  # Phrase-level relevance, without substring token matching.
            if score:
                ranked.append((score, memory))
        ranked.sort(key=lambda item: (-item[0], -item[1].updated_at.timestamp(), item[1].id))
        return [memory for _, memory in ranked[:limit]]

    @staticmethod
    def _candidate(row) -> MemoryCandidate:
        import json
        return MemoryCandidate(row[0], row[1], row[2], row[3], tuple(json.loads(row[4])),
                               datetime.fromisoformat(row[5]), row[6], row[7])

    @staticmethod
    def _candidate_identity(session_id, source_ids_json, content, category):
        import json
        normalized = " ".join(content.casefold().split())
        value = json.dumps([session_id, json.loads(source_ids_json), normalized, category.casefold().strip()],
                           ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def create_candidates(self, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
        import json
        stored = []
        with self._connect() as db:
            # Serialize the cross-turn identity check with inserts. This deliberately
            # deduplicates only normalized exact content within the same category.
            db.execute("BEGIN IMMEDIATE")
            for candidate in candidates:
                normalized_content = " ".join(candidate.content.casefold().split())
                normalized_category = candidate.category.casefold().strip()
                prior_rows = db.execute("SELECT * FROM memory_candidates WHERE lower(trim(category))=?",
                                        (normalized_category,)).fetchall()
                duplicate = next((row for row in prior_rows
                                  if " ".join(row[1].casefold().split()) == normalized_content), None)
                if duplicate is not None:
                    stored.append(self._candidate(duplicate))
                    continue
                source_ids = json.dumps(candidate.source_message_ids)
                key = self._candidate_identity(candidate.source_session_id, source_ids,
                                               candidate.content, candidate.category)
                db.execute("""INSERT OR IGNORE INTO memory_candidates
                            (id, content, category, source_session_id, source_message_ids, created_at, status, confidence, candidate_key)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (candidate.id, candidate.content, candidate.category, candidate.source_session_id,
                            source_ids, candidate.created_at.isoformat(), candidate.status, candidate.confidence, key))
                row = db.execute("SELECT * FROM memory_candidates WHERE candidate_key=?", (key,)).fetchone()
                if row:
                    stored.append(self._candidate(row))
        return stored

    def list_candidates(self, status: str | None = "pending") -> list[MemoryCandidate]:
        with self._connect() as db:
            if status is None:
                rows = db.execute("SELECT * FROM memory_candidates ORDER BY created_at, id").fetchall()
            else:
                rows = db.execute("SELECT * FROM memory_candidates WHERE status=? ORDER BY created_at, id", (status,)).fetchall()
        return [self._candidate(row) for row in rows]

    def get_candidate(self, candidate_id: str) -> MemoryCandidate | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM memory_candidates WHERE id=?", (candidate_id,)).fetchone()
        return self._candidate(row) if row else None

    def transition_candidate(self, candidate_id: str, status: str) -> MemoryCandidate:
        with self._connect() as db:
            row = db.execute("SELECT * FROM memory_candidates WHERE id=?", (candidate_id,)).fetchone()
            if row is None:
                raise KeyError(f"Candidate not found: {candidate_id}")
            current = self._candidate(row)
            if current.status == status:
                return current
            if current.status != "pending" or status not in {"confirmed", "rejected"}:
                raise ValueError(f"Invalid candidate transition: {current.status} -> {status}")
            db.execute("UPDATE memory_candidates SET status=? WHERE id=?", (status, candidate_id))
            row = db.execute("SELECT * FROM memory_candidates WHERE id=?", (candidate_id,)).fetchone()
        return self._candidate(row)

    @staticmethod
    def _task(row) -> CognitionTask:
        import json
        return CognitionTask(row[0], row[1], tuple(json.loads(row[2])), datetime.fromisoformat(row[3]),
                             row[4], row[5], row[6], datetime.fromisoformat(row[7]) if row[7] else None,
                             datetime.fromisoformat(row[8]) if row[8] else None)

    def enqueue_cognition_task(self, session_id: str, message_ids: tuple[str, ...], max_pending: int = 100) -> CognitionTask:
        import json
        source_ids = json.dumps(message_ids)
        with self._connect() as db:
            existing = db.execute("SELECT * FROM cognition_tasks WHERE source_session_id=? AND source_message_ids=?",
                                  (session_id, source_ids)).fetchone()
            if existing:
                return self._task(existing)
            pending = db.execute("SELECT count(*) FROM cognition_tasks WHERE status='pending'").fetchone()[0]
            if pending >= max_pending:
                raise OverflowError("Cognition task queue is full")
            now = datetime.now(timezone.utc)
            task_id = str(uuid4())
            db.execute("INSERT INTO cognition_tasks (id,source_session_id,source_message_ids,created_at,status) VALUES (?,?,?,?, 'pending')",
                       (task_id, session_id, source_ids, now.isoformat()))
            return self._task(db.execute("SELECT * FROM cognition_tasks WHERE id=?", (task_id,)).fetchone())

    def recover_cognition_tasks(self) -> int:
        with self._connect() as db:
            result = db.execute("UPDATE cognition_tasks SET status='pending', started_at=NULL WHERE status='running'")
            return result.rowcount

    def claim_cognition_task(self) -> CognitionTask | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cognition_tasks WHERE status='pending' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc).isoformat()
            db.execute("UPDATE cognition_tasks SET status='running', attempt_count=attempt_count+1, started_at=? WHERE id=? AND status='pending'",
                       (now, row[0]))
            updated = db.execute("SELECT * FROM cognition_tasks WHERE id=?", (row[0],)).fetchone()
            return self._task(updated)

    def finish_cognition_task(self, task_id: str, error: str | None = None, max_attempts: int = 2) -> None:
        status = "completed" if error is None else None
        with self._connect() as db:
            row = db.execute("SELECT attempt_count FROM cognition_tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            final_status = status or ("failed" if row[0] >= max_attempts else "pending")
            db.execute("UPDATE cognition_tasks SET status=?,last_error=?,completed_at=? WHERE id=?",
                       (final_status, error, datetime.now(timezone.utc).isoformat() if final_status in {"completed", "failed"} else None, task_id))

    def defer_cognition_task(self, task_id: str) -> None:
        """Return a claimed task to the durable queue when shutdown interrupts slot waiting."""
        with self._connect() as db:
            db.execute("""UPDATE cognition_tasks
                          SET status='pending', attempt_count=MAX(0, attempt_count-1),
                              started_at=NULL, completed_at=NULL
                          WHERE id=? AND status='running'""", (task_id,))

    def list_cognition_tasks(self, limit: int = 20) -> list[CognitionTask]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM cognition_tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._task(row) for row in rows]

    def get_cognition_task(self, task_id: str) -> CognitionTask | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cognition_tasks WHERE id=?", (task_id,)).fetchone()
        return self._task(row) if row else None

    def cognition_task_counts(self) -> dict[str, int]:
        with self._connect() as db:
            return dict(db.execute("SELECT status,count(*) FROM cognition_tasks GROUP BY status").fetchall())

    @staticmethod
    def _exploration_task(row) -> ExplorationTask:
        import json
        return ExplorationTask(row[0], row[1], tuple(json.loads(row[2])), datetime.fromisoformat(row[3]),
                               row[4], row[5], row[6], datetime.fromisoformat(row[7]) if row[7] else None)

    @staticmethod
    def _exploration_question(row) -> ExplorationQuestion:
        import json
        return ExplorationQuestion(row[0], row[1], row[2], tuple(json.loads(row[3])), tuple(json.loads(row[4])),
                                   datetime.fromisoformat(row[5]), row[6], row[7], datetime.fromisoformat(row[8]),
                                   row[9], row[10], row[12] if len(row) > 12 else None)

    @staticmethod
    def _question_identity(question: str) -> str:
        normalized = " ".join(question.casefold().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def enqueue_exploration_task(self, session_id: str, message_ids: tuple[str, ...], max_queue_items: int,
                                 cooldown_seconds: float, max_attempts_per_period: int,
                                 period_seconds: float) -> ExplorationTask | None:
        """Persist an eligible completed turn; return None when deterministic gates suppress it."""
        import json
        now = datetime.now(timezone.utc)
        stamp = now.isoformat()
        source_ids = json.dumps(message_ids)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE exploration_questions SET status='expired', notification_status='expired' "
                       "WHERE status='pending' AND expires_at<=?", (stamp,))
            existing = db.execute("SELECT * FROM exploration_tasks WHERE source_session_id=? AND source_message_ids=?",
                                  (session_id, source_ids)).fetchone()
            if existing:
                return self._exploration_task(existing)
            db.execute("DELETE FROM exploration_attempts WHERE attempted_at<?",
                       ((now - timedelta(seconds=period_seconds)).isoformat(),))
            attempts = db.execute("SELECT COUNT(*) FROM exploration_attempts WHERE attempted_at>=?",
                                  ((now - timedelta(seconds=period_seconds)).isoformat(),)).fetchone()[0]
            if attempts >= max_attempts_per_period:
                return None
            latest_attempt = db.execute("SELECT MAX(attempted_at) FROM exploration_attempts").fetchone()[0]
            latest_enqueue = db.execute("SELECT MAX(created_at) FROM exploration_tasks").fetchone()[0]
            recent = max((value for value in (latest_attempt, latest_enqueue) if value), default=None)
            if recent and (now - datetime.fromisoformat(recent)).total_seconds() < cooldown_seconds:
                return None
            queued = db.execute("SELECT COUNT(*) FROM exploration_tasks WHERE status='pending'").fetchone()[0]
            questions = db.execute("SELECT COUNT(*) FROM exploration_questions WHERE status='pending'").fetchone()[0]
            if queued + questions >= max_queue_items:
                return None
            task_id = str(uuid4())
            db.execute("INSERT INTO exploration_tasks (id,source_session_id,source_message_ids,created_at,status) VALUES (?,?,?,?, 'pending')",
                       (task_id, session_id, source_ids, stamp))
            return self._exploration_task(db.execute("SELECT * FROM exploration_tasks WHERE id=?", (task_id,)).fetchone())

    def recover_exploration_tasks(self, expiry_days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=expiry_days)).isoformat()
        with self._connect() as db:
            db.execute("UPDATE exploration_tasks SET status='expired',completed_at=? WHERE status IN ('pending','running') AND created_at<=?",
                       (datetime.now(timezone.utc).isoformat(), cutoff))
            result = db.execute("UPDATE exploration_tasks SET status='pending', started_at=NULL WHERE status='running'")
            return result.rowcount

    def claim_exploration_task(self, cooldown_seconds: float, max_attempts_per_period: int,
                               period_seconds: float, expiry_days: int = 30) -> tuple[ExplorationTask | None, float]:
        """Claim one task or return the event-wait duration until its rate gate opens."""
        now = datetime.now(timezone.utc)
        stamp = now.isoformat()
        cutoff = (now - timedelta(seconds=period_seconds)).isoformat()
        expiry_cutoff = (now - timedelta(days=expiry_days)).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE exploration_tasks SET status='expired',completed_at=? WHERE status='pending' AND created_at<=?",
                       (stamp, expiry_cutoff))
            db.execute("UPDATE exploration_questions SET status='expired' WHERE status='pending' AND expires_at<=?", (stamp,))
            row = db.execute("SELECT * FROM exploration_tasks WHERE status='pending' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                return None, 0.0
            db.execute("DELETE FROM exploration_attempts WHERE attempted_at<?", (cutoff,))
            attempts = db.execute("SELECT attempted_at FROM exploration_attempts WHERE attempted_at>=? ORDER BY attempted_at",
                                  (cutoff,)).fetchall()
            wait_seconds = 0.0
            if len(attempts) >= max_attempts_per_period:
                wait_seconds = max(wait_seconds, (datetime.fromisoformat(attempts[0][0]) + timedelta(seconds=period_seconds) - now).total_seconds())
            if attempts:
                wait_seconds = max(wait_seconds, (datetime.fromisoformat(attempts[-1][0]) + timedelta(seconds=cooldown_seconds) - now).total_seconds())
            if wait_seconds > 0:
                return None, wait_seconds
            task_id = row[0]
            db.execute("UPDATE exploration_tasks SET status='running', attempt_count=attempt_count+1, started_at=? WHERE id=? AND status='pending'",
                       (stamp, task_id))
            db.execute("INSERT INTO exploration_attempts VALUES (?,?,?)", (str(uuid4()), task_id, stamp))
            return self._exploration_task(db.execute("SELECT * FROM exploration_tasks WHERE id=?", (task_id,)).fetchone()), 0.0

    def finish_exploration_task(self, task_id: str, error: str | None = None) -> None:
        with self._connect() as db:
            row = db.execute("SELECT status FROM exploration_tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row[0] != "running":
                raise ValueError(f"Exploration task is {row[0]}")
            db.execute("UPDATE exploration_tasks SET status=?,last_error=?,completed_at=? WHERE id=?",
                       ("failed" if error else "completed", error, datetime.now(timezone.utc).isoformat(), task_id))

    def get_exploration_task(self, task_id: str) -> ExplorationTask | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM exploration_tasks WHERE id=?", (task_id,)).fetchone()
        return self._exploration_task(row) if row else None

    def list_exploration_tasks(self, limit: int = 20) -> list[ExplorationTask]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM exploration_tasks ORDER BY created_at DESC,id LIMIT ?", (limit,)).fetchall()
        return [self._exploration_task(row) for row in rows]

    def defer_exploration_task(self, task_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE exploration_tasks SET status='pending',started_at=NULL WHERE id=? AND status='running'", (task_id,))

    def create_exploration_question(self, question: ExplorationQuestion, max_queue_items: int) -> ExplorationQuestion | None:
        import json
        key = self._question_identity(question.question)
        stamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE exploration_questions SET status='expired' WHERE status='pending' AND expires_at<=?", (stamp,))
            existing = db.execute("SELECT * FROM exploration_questions WHERE question_key=?", (key,)).fetchone()
            if existing:
                return self._exploration_question(existing)
            pending_tasks = db.execute("SELECT COUNT(*) FROM exploration_tasks WHERE status='pending'").fetchone()[0]
            pending_questions = db.execute("SELECT COUNT(*) FROM exploration_questions WHERE status='pending'").fetchone()[0]
            pending = pending_tasks + pending_questions
            if pending >= max_queue_items:
                return None
            db.execute("""INSERT INTO exploration_questions
                (id,question,source_session_id,source_message_ids,source_memory_ids,created_at,status,attempt_count,
                 expires_at,topic,reason,question_key) VALUES (?,?,?,?,?,?,?, ?,?,?,?,?)""",
                (question.id, question.question, question.source_session_id, json.dumps(question.source_message_ids),
                 json.dumps(question.source_memory_ids), question.created_at.isoformat(), question.status,
                 question.attempt_count, question.expires_at.isoformat(), question.topic, question.reason, key))
            return self._exploration_question(db.execute("SELECT * FROM exploration_questions WHERE id=?", (question.id,)).fetchone())

    def list_exploration_questions(self, status: str | None = "pending") -> list[ExplorationQuestion]:
        with self._connect() as db:
            db.execute("UPDATE exploration_questions SET status='expired', notification_status='expired' "
                       "WHERE status='pending' AND expires_at<=?",
                       (datetime.now(timezone.utc).isoformat(),))
            if status is None:
                rows = db.execute("SELECT * FROM exploration_questions ORDER BY created_at,id").fetchall()
            else:
                rows = db.execute("SELECT * FROM exploration_questions WHERE status=? ORDER BY created_at,id", (status,)).fetchall()
        return [self._exploration_question(row) for row in rows]

    def get_exploration_question(self, question_id: str) -> ExplorationQuestion | None:
        self.list_exploration_questions(None)  # Apply expiry transitions consistently before retrieval.
        with self._connect() as db:
            row = db.execute("SELECT * FROM exploration_questions WHERE id=?", (question_id,)).fetchone()
        return self._exploration_question(row) if row else None

    def queue_exploration_notification(self, question_id: str) -> bool:
        """Durably claim the one allowed Presence notification for a live question."""
        stamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE exploration_questions SET status='expired', notification_status='expired' "
                       "WHERE status='pending' AND expires_at<=?", (stamp,))
            cursor = db.execute(
                "UPDATE exploration_questions SET notification_status='queued' "
                "WHERE id=? AND status='pending' AND expires_at>? AND notification_status IS NULL",
                (question_id, stamp),
            )
            return cursor.rowcount == 1

    def release_exploration_notification(self, question_id: str) -> None:
        """Undo a claim only when the bounded in-memory Presence queue rejected it."""
        stamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("UPDATE exploration_questions SET notification_status=? "
                       "WHERE id=? AND notification_status='queued' AND status='pending' AND expires_at>?",
                       (None, question_id, stamp))

    def mark_exploration_notification_delivered(self, question_id: str) -> bool:
        """Claim delivery before printing, preventing a restart from repeating it."""
        stamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE exploration_questions SET notification_status='delivered' "
                "WHERE id=? AND notification_status='queued' AND status='pending' AND expires_at>?",
                (question_id, stamp),
            )
            return cursor.rowcount == 1

    def dismiss_exploration_notification(self, question_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE exploration_questions SET notification_status='dismissed' "
                "WHERE id=? AND notification_status IN ('queued','dismissed','expired')",
                (question_id,),
            )
            return cursor.rowcount == 1

    def list_queued_exploration_notifications(self) -> list[ExplorationQuestion]:
        """Return saved, still-eligible questions whose notice awaits prompt delivery."""
        stamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("UPDATE exploration_questions SET status='expired', notification_status='expired' "
                       "WHERE status='pending' AND expires_at<=?", (stamp,))
            rows = db.execute(
                "SELECT * FROM exploration_questions WHERE status='pending' "
                "AND notification_status='queued' AND expires_at>? ORDER BY created_at,id",
                (stamp,),
            ).fetchall()
        return [self._exploration_question(row) for row in rows]

    def dismiss_exploration_question(self, question_id: str) -> ExplorationQuestion:
        self.list_exploration_questions(None)
        with self._connect() as db:
            row = db.execute("SELECT status FROM exploration_questions WHERE id=?", (question_id,)).fetchone()
            if row is None:
                raise KeyError(f"Exploration question not found: {question_id}")
            if row[0] != "pending":
                raise ValueError(f"Exploration question is {row[0]}")
            db.execute("UPDATE exploration_questions SET status='dismissed', "
                       "notification_status=CASE WHEN notification_status IS NULL OR notification_status='queued' "
                       "THEN 'dismissed' ELSE notification_status END WHERE id=?", (question_id,))
        return self.get_exploration_question(question_id)  # type: ignore[return-value]

    def exploration_counts(self) -> dict[str, int]:
        self.list_exploration_questions(None)
        with self._connect() as db:
            task_counts = dict(db.execute("SELECT status,count(*) FROM exploration_tasks GROUP BY status").fetchall())
            question_counts = dict(db.execute("SELECT status,count(*) FROM exploration_questions GROUP BY status").fetchall())
        return {**{f"task_{key}": value for key, value in task_counts.items()},
                **{f"question_{key}": value for key, value in question_counts.items()}}

    @staticmethod
    def _investigation(row) -> Investigation:
        budget = InvestigationBudget(row[11], row[12], row[13], row[14], row[15], row[16])
        return Investigation(
            row[0], row[1], row[2], InvestigationStatus(row[3]),
            datetime.fromisoformat(row[4]), datetime.fromisoformat(row[5]), datetime.fromisoformat(row[6]),
            row[7], row[8], budget, row[9], datetime.fromisoformat(row[10]) if row[10] else None,
            row[18], row[19], row[20], row[21], row[22],
            datetime.fromisoformat(row[17]) if row[17] else None,
        )

    _INVESTIGATION_SELECT = """SELECT id,source_question_id,question,status,created_at,updated_at,expires_at,
        attempt_count,max_attempts,last_error,deferred_at,max_duration_seconds,max_model_calls,
        max_search_requests,max_pages,max_source_bytes,max_extracted_text_chars,started_at,search_calls_used,
        pages_read,model_calls_used,source_bytes_used,extracted_chars_used FROM investigations"""

    def create_investigation(self, question_id: str, budget: InvestigationBudget,
                             max_attempts: int = 3) -> Investigation:
        """Queue one investigation for an existing, unexpired pending Exploration question."""
        now = datetime.now(timezone.utc)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(self._INVESTIGATION_SELECT + " WHERE source_question_id=?", (question_id,)).fetchone()
            if existing:
                return self._investigation(existing)
            question = db.execute(
                "SELECT question,status,expires_at FROM exploration_questions WHERE id=?", (question_id,)
            ).fetchone()
            if question is None:
                raise KeyError(f"Exploration question not found: {question_id}")
            expiry = datetime.fromisoformat(question[2])
            if question[1] != "pending" or expiry <= now:
                raise ValueError(f"Exploration question is {question[1] if question[1] != 'pending' else 'expired'}")
            investigation = Investigation.queued(question_id, question[0], budget, max_attempts, now)
            limits = investigation.budget.as_dict()
            db.execute("""INSERT INTO investigations (
                id,source_question_id,question,status,created_at,updated_at,expires_at,attempt_count,max_attempts,
                last_error,deferred_at,max_duration_seconds,max_model_calls,max_search_requests,max_pages,
                max_source_bytes,max_extracted_text_chars
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                investigation.id, investigation.source_question_id, investigation.question,
                investigation.status.value, investigation.created_at.isoformat(), investigation.updated_at.isoformat(),
                investigation.expires_at.isoformat(), investigation.attempt_count, investigation.max_attempts,
                investigation.last_error, None, *limits.values(),
            ))
            row = db.execute(self._INVESTIGATION_SELECT + " WHERE id=?", (investigation.id,)).fetchone()
            return self._investigation(row)

    def get_investigation(self, investigation_id: str) -> Investigation | None:
        with self._connect() as db:
            row = db.execute(self._INVESTIGATION_SELECT + " WHERE id=?", (investigation_id,)).fetchone()
        return self._investigation(row) if row else None

    def get_investigation_for_question(self, question_id: str) -> Investigation | None:
        with self._connect() as db:
            row = db.execute(self._INVESTIGATION_SELECT + " WHERE source_question_id=?", (question_id,)).fetchone()
        return self._investigation(row) if row else None

    def list_investigations(self, status: InvestigationStatus | None = None,
                            limit: int = 100) -> list[Investigation]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._connect() as db:
            if status is None:
                rows = db.execute(self._INVESTIGATION_SELECT + " ORDER BY created_at DESC,id LIMIT ?", (limit,)).fetchall()
            else:
                value = InvestigationStatus(status).value
                rows = db.execute(self._INVESTIGATION_SELECT + " WHERE status=? ORDER BY created_at DESC,id LIMIT ?",
                                  (value, limit)).fetchall()
        return [self._investigation(row) for row in rows]

    @staticmethod
    def _write_investigation(db, item: Investigation) -> None:
        db.execute("""UPDATE investigations SET status=?,updated_at=?,attempt_count=?,last_error=?,deferred_at=?,
            started_at=?,search_calls_used=?,pages_read=?,model_calls_used=?,source_bytes_used=?,extracted_chars_used=?
            WHERE id=?""", (item.status.value, item.updated_at.isoformat(), item.attempt_count,
                              item.last_error, item.deferred_at.isoformat() if item.deferred_at else None,
                              item.started_at.isoformat() if item.started_at else None, item.search_calls_used,
                              item.pages_read, item.model_calls_used, item.source_bytes_used,
                              item.extracted_chars_used, item.id))

    def transition_investigation(self, investigation_id: str, target: InvestigationStatus,
                                 error: str | None = None) -> Investigation:
        now = datetime.now(timezone.utc)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(self._INVESTIGATION_SELECT + " WHERE id=?", (investigation_id,)).fetchone()
            if row is None:
                raise KeyError(f"Investigation not found: {investigation_id}")
            current = self._investigation(row)
            if target == InvestigationStatus.RUNNING and current.expires_at <= now:
                if current.status in {InvestigationStatus.QUEUED, InvestigationStatus.RUNNING}:
                    current = current.transition(InvestigationStatus.EXPIRED, now)
                    self._write_investigation(db, current)
                return current
            updated = current.transition(target, now, error)
            self._write_investigation(db, updated)
            return updated

    def cancel_investigation(self, investigation_id: str) -> Investigation:
        return self.transition_investigation(investigation_id, InvestigationStatus.CANCELLED)

    def recover_investigations(self) -> int:
        """Expire stale work and requeue interrupted work within its persisted attempt limit."""
        now = datetime.now(timezone.utc)
        changed = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(self._INVESTIGATION_SELECT +
                              " WHERE status IN ('queued','running') ORDER BY created_at,id").fetchall()
            for row in rows:
                item = self._investigation(row)
                if item.expires_at <= now:
                    updated = item.transition(InvestigationStatus.EXPIRED, now)
                elif item.status == InvestigationStatus.RUNNING:
                    if item.attempt_count >= item.max_attempts:
                        updated = item.transition(InvestigationStatus.FAILED, now,
                                                  "Interrupted after the final allowed attempt")
                    else:
                        updated = item.transition(InvestigationStatus.QUEUED, now)
                else:
                    continue
                self._write_investigation(db, updated)
                changed += 1
        return changed

    def defer_running_investigations(self) -> int:
        """Persist running work as deferred during shutdown; does not execute investigations."""
        now = datetime.now(timezone.utc)
        changed = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(self._INVESTIGATION_SELECT + " WHERE status='running'").fetchall()
            for row in rows:
                item = self._investigation(row)
                if item.expires_at <= now:
                    updated = item.transition(InvestigationStatus.EXPIRED, now)
                elif item.attempt_count >= item.max_attempts:
                    updated = item.transition(InvestigationStatus.FAILED, now,
                                              "Interrupted after the final allowed attempt")
                else:
                    updated = item.transition(InvestigationStatus.QUEUED, now)
                self._write_investigation(db, updated)
                changed += 1
        return changed

    def charge_investigation_budget(self, investigation_id: str, *, search_calls: int = 0,
                                    pages_read: int = 0, model_calls: int = 0,
                                    source_bytes: int = 0, extracted_chars: int = 0) -> Investigation:
        increments = {
            "search_calls_used": search_calls,
            "pages_read": pages_read,
            "model_calls_used": model_calls,
            "source_bytes_used": source_bytes,
            "extracted_chars_used": extracted_chars,
        }
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in increments.values()):
            raise ValueError("Investigation budget charges must be non-negative integers")
        maxima = {
            "search_calls_used": "max_search_requests",
            "pages_read": "max_pages",
            "model_calls_used": "max_model_calls",
            "source_bytes_used": "max_source_bytes",
            "extracted_chars_used": "max_extracted_text_chars",
        }
        now = datetime.now(timezone.utc)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(self._INVESTIGATION_SELECT + " WHERE id=?", (investigation_id,)).fetchone()
            if row is None:
                raise KeyError(f"Investigation not found: {investigation_id}")
            item = self._investigation(row)
            if item.status != InvestigationStatus.RUNNING:
                raise ValueError(f"Investigation is {item.status.value}")
            if item.expires_at <= now:
                self._write_investigation(db, item.transition(InvestigationStatus.EXPIRED, now))
                return self._investigation(db.execute(
                    self._INVESTIGATION_SELECT + " WHERE id=?", (investigation_id,)
                ).fetchone())
            for used_field, increment in increments.items():
                maximum = getattr(item.budget, maxima[used_field])
                if getattr(item, used_field) + increment > maximum:
                    raise InvestigationBudgetError(f"Investigation budget exhausted: {used_field}")
            db.execute("""UPDATE investigations SET updated_at=?,search_calls_used=search_calls_used+?,
                pages_read=pages_read+?,model_calls_used=model_calls_used+?,source_bytes_used=source_bytes_used+?,
                extracted_chars_used=extracted_chars_used+? WHERE id=?""",
                (now.isoformat(), search_calls, pages_read, model_calls, source_bytes, extracted_chars,
                 investigation_id))
            return self._investigation(db.execute(
                self._INVESTIGATION_SELECT + " WHERE id=?", (investigation_id,)
            ).fetchone())

    @staticmethod
    def _investigation_evidence(row) -> InvestigationEvidence:
        return InvestigationEvidence(
            row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
            datetime.fromisoformat(row[8]), datetime.fromisoformat(row[9]) if row[9] else None,
            row[10], row[11], row[12], row[13], row[14], row[15],
        )

    _INVESTIGATION_EVIDENCE_SELECT = """SELECT id,investigation_id,source_url,provider,search_rank,title,snippet,
        status,selected_at,retrieved_at,final_url,content_type,text,content_hash,source_bytes,error
        FROM investigation_evidence"""

    def create_or_get_investigation_evidence(self, evidence: InvestigationEvidence) -> InvestigationEvidence:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT OR IGNORE INTO investigation_evidence (
                id,investigation_id,source_url,provider,search_rank,title,snippet,status,selected_at,
                retrieved_at,final_url,content_type,text,content_hash,source_bytes,error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                evidence.id, evidence.investigation_id, evidence.source_url, evidence.provider,
                evidence.search_rank, evidence.title, evidence.snippet, evidence.status,
                evidence.selected_at.isoformat(), evidence.retrieved_at.isoformat() if evidence.retrieved_at else None,
                evidence.final_url, evidence.content_type, evidence.text, evidence.content_hash,
                evidence.source_bytes, evidence.error,
            ))
            row = db.execute(self._INVESTIGATION_EVIDENCE_SELECT +
                             " WHERE investigation_id=? AND source_url=?",
                             (evidence.investigation_id, evidence.source_url)).fetchone()
            return self._investigation_evidence(row)

    def update_investigation_evidence(self, evidence: InvestigationEvidence) -> InvestigationEvidence:
        with self._connect() as db:
            updated = db.execute("""UPDATE investigation_evidence SET provider=?,search_rank=?,title=?,snippet=?,
                status=?,selected_at=?,retrieved_at=?,final_url=?,content_type=?,text=?,content_hash=?,source_bytes=?,error=?
                WHERE id=?""", (
                evidence.provider, evidence.search_rank, evidence.title, evidence.snippet, evidence.status,
                evidence.selected_at.isoformat(), evidence.retrieved_at.isoformat() if evidence.retrieved_at else None,
                evidence.final_url, evidence.content_type, evidence.text, evidence.content_hash,
                evidence.source_bytes, evidence.error, evidence.id,
            ))
            if updated.rowcount == 0:
                raise KeyError(f"Investigation evidence not found: {evidence.id}")
            row = db.execute(self._INVESTIGATION_EVIDENCE_SELECT + " WHERE id=?", (evidence.id,)).fetchone()
        return self._investigation_evidence(row)

    def list_investigation_evidence(self, investigation_id: str) -> list[InvestigationEvidence]:
        with self._connect() as db:
            rows = db.execute(self._INVESTIGATION_EVIDENCE_SELECT +
                              " WHERE investigation_id=? ORDER BY search_rank,id", (investigation_id,)).fetchall()
        return [self._investigation_evidence(row) for row in rows]

    @staticmethod
    def _investigation_result(row) -> InvestigationResult | None:
        if row is None:
            return None
        import json
        findings = tuple(InvestigationFinding(value["text"], tuple(value["evidence_ids"]))
                         for value in json.loads(row[2]))
        return InvestigationResult(row[0], row[1], findings, row[3], tuple(json.loads(row[4])),
                                   bool(row[5]), datetime.fromisoformat(row[6]))

    def get_investigation_result(self, investigation_id: str) -> InvestigationResult | None:
        with self._connect() as db:
            row = db.execute("""SELECT investigation_id,summary,findings_json,uncertainty,limitations_json,
                evidence_sufficient,created_at FROM investigation_results WHERE investigation_id=?""",
                             (investigation_id,)).fetchone()
        return self._investigation_result(row)

    def complete_investigation(self, result: InvestigationResult) -> InvestigationResult | None:
        import json
        now = datetime.now(timezone.utc)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(self._INVESTIGATION_SELECT + " WHERE id=?", (result.investigation_id,)).fetchone()
            if row is None:
                raise KeyError(f"Investigation not found: {result.investigation_id}")
            item = self._investigation(row)
            if item.status != InvestigationStatus.RUNNING:
                return self._investigation_result(db.execute(
                    """SELECT investigation_id,summary,findings_json,uncertainty,limitations_json,
                    evidence_sufficient,created_at FROM investigation_results WHERE investigation_id=?""",
                    (result.investigation_id,)).fetchone())
            if item.expires_at <= now:
                self._write_investigation(db, item.transition(InvestigationStatus.EXPIRED, now))
                return None
            allowed = {value[0] for value in db.execute(
                "SELECT id FROM investigation_evidence WHERE investigation_id=? AND status='retrieved'",
                (result.investigation_id,)).fetchall()}
            if any(not set(finding.evidence_ids).issubset(allowed) for finding in result.findings):
                raise ValueError("Investigation result references evidence not stored for this investigation")
            db.execute("""INSERT INTO investigation_results
                (investigation_id,summary,findings_json,uncertainty,limitations_json,evidence_sufficient,created_at)
                VALUES (?,?,?,?,?,?,?)""", (
                result.investigation_id, result.summary,
                json.dumps([{"text": finding.text, "evidence_ids": finding.evidence_ids}
                            for finding in result.findings], ensure_ascii=False),
                result.uncertainty, json.dumps(result.limitations, ensure_ascii=False),
                int(result.evidence_sufficient), result.created_at.isoformat(),
            ))
            self._write_investigation(db, item.transition(InvestigationStatus.COMPLETED, now))
            return self._investigation_result(db.execute(
                """SELECT investigation_id,summary,findings_json,uncertainty,limitations_json,
                evidence_sufficient,created_at FROM investigation_results WHERE investigation_id=?""",
                (result.investigation_id,)).fetchone())
