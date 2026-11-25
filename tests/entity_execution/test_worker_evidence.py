from __future__ import annotations

import hashlib
import json

import pytest

from sylphy_entity.contracts.execution import PermissionClass
from sylphy_entity.execution.permissions import PermissionPolicy
from sylphy_entity.execution.worker_executor import SandboxWorkerExecutor
from sylphy_entity.runtime.process_manager import WorkerResult, WorkerState


class ForgedProcessManager:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.forgotten: list[str] = []

    async def start(self, name, argv, *, cwd=None, env=None):
        self.name = name

    async def wait(self, name):
        return WorkerResult(
            name=name,
            pid=43210,
            return_code=0,
            stdout=self.stdout,
            stderr="",
            state=WorkerState.EXITED,
            create_time=1234.5,
        )

    async def forget(self, name):
        self.forgotten.append(name)


@pytest.mark.asyncio
async def test_worker_executor_recomputes_returned_content_hash(sandbox, intent_factory):
    policy = PermissionPolicy(sandbox)
    intent = intent_factory(
        "notes.txt",
        capability="read_sandbox_text",
        permission=PermissionClass.READ_SANDBOX,
        scope=policy.scope,
    )
    payload = {
        "relative_path": "notes.txt",
        "size_bytes": 6,
        "modified_ns": 1,
        "is_file": True,
        "text": "forged",
        "content_sha256": "0" * 64,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["evidence_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    manager = ForgedProcessManager(json.dumps(payload, ensure_ascii=False) + "\n")
    executor = SandboxWorkerExecutor(manager, read_limit_bytes=1024)

    result = await executor(intent, policy.authorize(intent))

    assert result.status.value == "failed"
    assert "content hash or byte count" in (result.error or "")
    assert manager.forgotten == ["intent-intent-test-1"]
