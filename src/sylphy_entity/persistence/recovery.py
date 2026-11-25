"""Crash recovery and conservative process/Intent reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Callable
from uuid import uuid4

import psutil

from .codec import canonical_json
from .database import EntityDatabase
from .event_store import EventStore

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    recovery_id: str
    previous_shutdown_clean: bool | None
    interrupted_intent_ids: tuple[str, ...]
    unknown_intent_ids: tuple[str, ...]
    terminated_worker_ids: tuple[str, ...]
    missing_worker_ids: tuple[str, ...]
    unverified_worker_ids: tuple[str, ...]
    abandoned_lease_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "recovery_id": self.recovery_id,
            "previous_shutdown_clean": self.previous_shutdown_clean,
            "interrupted_intent_ids": list(self.interrupted_intent_ids),
            "unknown_intent_ids": list(self.unknown_intent_ids),
            "terminated_worker_ids": list(self.terminated_worker_ids),
            "missing_worker_ids": list(self.missing_worker_ids),
            "unverified_worker_ids": list(self.unverified_worker_ids),
            "abandoned_lease_count": self.abandoned_lease_count,
        }


class RecoveryManager:
    """Never retries an external side effect automatically after a crash."""

    def __init__(
        self,
        database: EntityDatabase,
        event_store: EventStore,
        *,
        process_matches: Callable[[int, float], bool] | None = None,
        terminate_verified_process: Callable[[int, float], bool] | None = None,
    ) -> None:
        self.database = database
        self.event_store = event_store
        self._process_matches = process_matches or self._default_process_matches
        self._terminate = terminate_verified_process or self._default_terminate_verified

    def recover(self, previous_shutdown_clean: bool | None) -> RecoveryReport:
        started = _now()
        recovery_id = uuid4().hex
        interrupted: list[str] = []
        unknown: list[str] = []
        for record in self.event_store.list_incomplete_intents():
            intent_id = record["intent_id"]
            if record["status"] == "ACCEPTED":
                self.event_store.mark_intent_recovered(
                    intent_id, "INTERRUPTED", "accepted before shutdown but never durably entered dispatch"
                )
                interrupted.append(intent_id)
            else:
                self.event_store.mark_intent_recovered(
                    intent_id, "UNKNOWN", "daemon stopped while execution outcome was not durably verified"
                )
                unknown.append(intent_id)

        terminated: list[str] = []
        missing: list[str] = []
        unverified: list[str] = []
        for worker in self.event_store.active_workers():
            worker_id = worker["worker_id"]
            pid = worker["pid"]
            create_time = worker["create_time"]
            if create_time is None:
                # A PID alone is never a sufficient process identity.
                self.event_store.mark_worker_recovered(worker_id, "UNVERIFIED_ORPHAN")
                unverified.append(worker_id)
                continue
            try:
                matches = self._process_matches(pid, float(create_time))
            except Exception:
                matches = False
            if not matches:
                self.event_store.mark_worker_recovered(worker_id, "NOT_RUNNING_OR_PID_REUSED")
                missing.append(worker_id)
                continue
            try:
                stopped = self._terminate(pid, float(create_time))
            except Exception:
                logger.exception("Could not terminate verified orphan worker pid=%s", pid)
                stopped = False
            if stopped:
                self.event_store.mark_worker_recovered(worker_id, "ORPHAN_TERMINATED")
                terminated.append(worker_id)
            else:
                self.event_store.mark_worker_recovered(worker_id, "VERIFIED_ORPHAN_TERMINATION_FAILED")
                unverified.append(worker_id)

        abandoned_leases = self.event_store.mark_active_leases_abandoned()
        report = RecoveryReport(
            recovery_id=recovery_id,
            previous_shutdown_clean=previous_shutdown_clean,
            interrupted_intent_ids=tuple(interrupted),
            unknown_intent_ids=tuple(unknown),
            terminated_worker_ids=tuple(terminated),
            missing_worker_ids=tuple(missing),
            unverified_worker_ids=tuple(unverified),
            abandoned_lease_count=abandoned_leases,
        )
        finished = _now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO recovery_runs(recovery_id,started_at,finished_at,previous_shutdown_clean,report_json)
                   VALUES(?,?,?,?,?)""",
                (recovery_id,started,finished,
                 None if previous_shutdown_clean is None else int(previous_shutdown_clean),
                 canonical_json(report.to_dict())),
            )
        return report

    @staticmethod
    def _default_process_matches(pid: int, create_time: float) -> bool:
        try:
            process = psutil.Process(pid)
            return abs(process.create_time() - create_time) < 1.0 and process.is_running()
        except psutil.Error:
            return False

    @staticmethod
    def _default_terminate_verified(pid: int, create_time: float) -> bool:
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - create_time) >= 1.0 or not process.is_running():
                return False
            children = process.children(recursive=True)
            for child in reversed(children):
                try:
                    child.terminate()
                except psutil.Error:
                    pass
            process.terminate()
            gone, alive = psutil.wait_procs([process, *children], timeout=2.0)
            for item in alive:
                try:
                    item.kill()
                except psutil.Error:
                    pass
            if alive:
                psutil.wait_procs(alive, timeout=2.0)
            return not psutil.pid_exists(pid)
        except psutil.Error:
            return False
