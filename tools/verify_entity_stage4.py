"""Cross-platform Stage 4 execution-boundary smoke test."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from sylphy_entity.contracts.decision import IntentProposal
from sylphy_entity.contracts.execution import ExecutionStatus, PermissionClass
from sylphy_entity.execution.dispatcher import IntentDispatcher
from sylphy_entity.execution.intent_router import IntentRouter
from sylphy_entity.execution.permissions import PermissionPolicy
from sylphy_entity.execution.resource_arbiter import ResourceArbiter
from sylphy_entity.execution.worker_executor import SandboxWorkerExecutor
from sylphy_entity.runtime.process_manager import ProcessManager


def proposal(scope: str, path: str, capability: str, permission: str, *, intent_id: str) -> IntentProposal:
    return IntentProposal(
        intent_id=intent_id,
        goal_id="stage4-smoke-goal",
        capability=capability,
        target=path,
        arguments=(("relative_path", path),),
        reason="Stage 4 Windows smoke test",
        permission_class=permission,
        permission_scope=scope,
        required_resources=(f"filesystem:sandbox:{path}",),
        expected_outcome="verified sandbox result",
        priority=0.5,
    )


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sylphy-stage4-") as temp:
        root = Path(temp).resolve()
        note = root / "notes.txt"
        note.write_text("Stage 4 worker verification", encoding="utf-8")
        policy = PermissionPolicy(root, max_read_bytes=1024)
        process_manager = ProcessManager()
        executor = SandboxWorkerExecutor(process_manager, read_limit_bytes=1024)
        dispatcher = IntentDispatcher(
            permission_policy=policy,
            resource_arbiter=ResourceArbiter(),
            handlers={
                "inspect_file_metadata": executor,
                "read_sandbox_text": executor,
            },
        )
        router = IntentRouter(timeout_seconds=10, deadline_seconds=30)
        try:
            metadata_intent = router.route(proposal(
                policy.scope, "notes.txt", "inspect_file_metadata", "OBSERVE_SANDBOX",
                intent_id="stage4-metadata",
            ))
            metadata = await dispatcher.execute(metadata_intent)
            assert metadata.status is ExecutionStatus.SUCCEEDED, metadata
            assert metadata.actual_result["relative_path"] == "notes.txt"
            assert metadata.evidence_ids and metadata.worker_pid and metadata.worker_return_code == 0
            assert metadata.worker_create_time is not None, "worker process creation time was not recorded"
            print("PASS: sandbox metadata inspection executed in isolated worker with PID and creation-time identity")

            read_intent = router.route(proposal(
                policy.scope, "notes.txt", "read_sandbox_text", "READ_SANDBOX",
                intent_id="stage4-read",
            ))
            read_result = await dispatcher.execute(read_intent)
            assert read_result.status is ExecutionStatus.SUCCEEDED, read_result
            assert read_result.actual_result["text"] == "Stage 4 worker verification"
            assert read_result.actual_result["content_sha256"]
            assert read_result.worker_pid and read_result.worker_create_time is not None
            print("PASS: bounded sandbox text read returned content hash evidence")

            from dataclasses import replace
            mismatched_scope_intent = router.route(proposal(
                policy.scope, "notes.txt", "inspect_file_metadata", "OBSERVE_SANDBOX",
                intent_id="stage4-denied",
            ))
            outside_intent = replace(mismatched_scope_intent, permission_scope="sandbox:/outside")
            denied = await dispatcher.execute(outside_intent)
            assert denied.status is ExecutionStatus.REJECTED
            print("PASS: mismatched permission scope rejected before execution")

            policy.revoke_scope(policy.scope)
            revoked_intent = router.route(proposal(
                policy.scope, "notes.txt", "inspect_file_metadata", "OBSERVE_SANDBOX",
                intent_id="stage4-revoked",
            ))
            revoked = await dispatcher.execute(revoked_intent)
            assert revoked.status is ExecutionStatus.REJECTED
            print("PASS: revoked sandbox scope rejected")

            assert process_manager.snapshots() == []
            print("PASS: worker processes cleaned up")
            print("PASS: Stage 4 execution-boundary smoke test completed")
        finally:
            await process_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
