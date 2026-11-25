"""Run the allowlisted sandbox inspection worker without a shell."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import sys
from typing import Callable

from sylphy_entity.contracts.execution import ExecutionResult, ExecutionStatus, Intent
from sylphy_entity.execution.permissions import AuthorizedTarget
from sylphy_entity.runtime.process_manager import ProcessManager, WorkerState


class SandboxWorkerExecutor:
    def __init__(
        self,
        process_manager: ProcessManager,
        *,
        read_limit_bytes: int = 64 * 1024,
        on_worker_started: Callable[[Intent, object], None] | None = None,
    ) -> None:
        if read_limit_bytes < 1:
            raise ValueError("read_limit_bytes must be positive")
        self._process_manager = process_manager
        self._read_limit = read_limit_bytes
        self._on_worker_started = on_worker_started

    async def __call__(self, intent: Intent, target: AuthorizedTarget) -> ExecutionResult:
        mode = "metadata" if intent.capability == "inspect_file_metadata" else "read_text"
        if intent.capability not in {"inspect_file_metadata", "read_sandbox_text"}:
            return ExecutionResult.immediate(
                intent.intent_id,
                ExecutionStatus.REJECTED,
                error=f"unsupported worker capability: {intent.capability}",
            )
        worker_name = f"intent-{intent.intent_id}"
        argv = [
            sys.executable,
            "-m",
            "sylphy_entity.execution.workers.file_inspector",
            "--sandbox-root",
            str(target.root),
            "--relative-path",
            target.relative_path,
            "--mode",
            mode,
            "--max-bytes",
            str(self._read_limit),
        ]
        started = datetime.now(timezone.utc)
        # The worker runs with the sandbox as cwd, so a source checkout launched
        # through PYTHONPATH would otherwise lose the package root in the child.
        # Preserve the caller's environment and add this checkout's import root;
        # installed deployments continue to work through their normal site-packages.
        worker_env = os.environ.copy()
        source_root = str(Path(__file__).resolve().parents[2])
        inherited_pythonpath = worker_env.get("PYTHONPATH")
        worker_env["PYTHONPATH"] = (
            source_root if not inherited_pythonpath
            else source_root + os.pathsep + inherited_pythonpath
        )
        worker_snapshot = await self._process_manager.start(
            worker_name, argv, cwd=str(target.root), env=worker_env
        )
        try:
            if self._on_worker_started is not None:
                try:
                    self._on_worker_started(intent, worker_snapshot)
                except Exception:
                    await self._process_manager.stop(worker_name)
                    raise
            try:
                worker = await asyncio.wait_for(
                    self._process_manager.wait(worker_name),
                    timeout=intent.timeout_seconds,
                )
            except asyncio.TimeoutError:
                worker = await self._process_manager.stop(worker_name)
                return self._failed_result(intent, started, worker, "worker timed out")
            except asyncio.CancelledError:
                await self._process_manager.stop(worker_name)
                raise
            finished = datetime.now(timezone.utc)
            log_material = (worker.stdout + "\n" + worker.stderr).encode("utf-8", errors="replace")
            log_evidence = "worker-log:sha256:" + hashlib.sha256(log_material).hexdigest()
            if worker.state is not WorkerState.EXITED or worker.return_code != 0:
                return ExecutionResult(
                    intent_id=intent.intent_id,
                    status=ExecutionStatus.FAILED,
                    started_at=started,
                    finished_at=finished,
                    actual_result={"return_code": worker.return_code, "stderr": worker.stderr[-2048:]},
                    evidence_ids=(log_evidence,),
                    error=f"worker exited with {worker.state.value} ({worker.return_code})",
                    worker_pid=worker.pid,
                    worker_create_time=worker.create_time,
                    worker_return_code=worker.return_code,
                )
            try:
                payload = json.loads(worker.stdout)
                if not isinstance(payload, dict):
                    raise ValueError("worker result must be a JSON object")
                evidence_id = payload.pop("evidence_id")
                if not isinstance(evidence_id, str):
                    raise ValueError("worker did not return a verifiable evidence ID")
                canonical_evidence = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                expected_evidence_id = "sha256:" + hashlib.sha256(canonical_evidence).hexdigest()
                if evidence_id != expected_evidence_id:
                    raise ValueError("worker evidence hash does not match its result payload")
                if payload.get("relative_path") != target.relative_path or payload.get("is_file") is not True:
                    raise ValueError("worker result does not match authorized target")
                expected_fields = {"relative_path", "size_bytes", "modified_ns", "is_file"}
                if mode == "read_text":
                    expected_fields.update({"text", "content_sha256"})
                if set(payload) != expected_fields:
                    raise ValueError("worker result contains missing or unexpected fields")
                size_bytes = payload.get("size_bytes")
                modified_ns = payload.get("modified_ns")
                if type(size_bytes) is not int or size_bytes < 0:
                    raise ValueError("worker returned an invalid file size")
                if type(modified_ns) is not int or modified_ns < 0:
                    raise ValueError("worker returned invalid modification metadata")
                if mode == "read_text":
                    text_value = payload.get("text")
                    content_digest = payload.get("content_sha256")
                    if not isinstance(text_value, str) or not isinstance(content_digest, str):
                        raise ValueError("worker returned invalid text evidence")
                    content_bytes = text_value.encode("utf-8")
                    if len(content_bytes) > self._read_limit:
                        raise ValueError("worker returned text exceeding the configured read limit")
                    expected_content_digest = hashlib.sha256(content_bytes).hexdigest()
                    if len(content_bytes) != size_bytes or content_digest != expected_content_digest:
                        raise ValueError("worker content hash or byte count does not match returned text")
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                return ExecutionResult(
                    intent_id=intent.intent_id,
                    status=ExecutionStatus.FAILED,
                    started_at=started,
                    finished_at=finished,
                    actual_result={"stdout": worker.stdout[-2048:], "stderr": worker.stderr[-2048:]},
                    evidence_ids=(log_evidence,),
                    error=f"invalid worker result: {exc}",
                    worker_pid=worker.pid,
                    worker_create_time=worker.create_time,
                    worker_return_code=worker.return_code,
                )
            actual: dict[str, str | int | float | bool | None] = {}
            for key, value in payload.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    actual[key] = value
            return ExecutionResult(
                intent_id=intent.intent_id,
                status=ExecutionStatus.SUCCEEDED,
                started_at=started,
                finished_at=finished,
                actual_result=actual,
                evidence_ids=(evidence_id, log_evidence),
                worker_pid=worker.pid,
                worker_create_time=worker.create_time,
                worker_return_code=worker.return_code,
            )
        finally:
            await self._process_manager.forget(worker_name)

    @staticmethod
    def _failed_result(intent: Intent, started, worker, reason: str) -> ExecutionResult:
        digest = hashlib.sha256((worker.stdout + "\n" + worker.stderr).encode()).hexdigest()
        return ExecutionResult(
            intent_id=intent.intent_id,
            status=ExecutionStatus.FAILED,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            actual_result={"return_code": worker.return_code, "stderr": worker.stderr[-2048:]},
            evidence_ids=(f"worker-log:sha256:{digest}",),
            error=reason,
            worker_pid=worker.pid,
            worker_create_time=worker.create_time,
            worker_return_code=worker.return_code,
        )
