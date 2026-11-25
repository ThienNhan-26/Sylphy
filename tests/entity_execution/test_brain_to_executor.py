from datetime import datetime, timezone

import pytest

from sylphy_entity.brain import EntityBrain
from sylphy_entity.contracts.decision import BrainState
from sylphy_entity.contracts.execution import ExecutionStatus
from sylphy_entity.contracts.world import FactState, WorldFact, WorldSnapshot
from sylphy_entity.execution.dispatcher import IntentDispatcher
from sylphy_entity.execution.intent_router import IntentRouter
from sylphy_entity.execution.permissions import PermissionPolicy
from sylphy_entity.execution.resource_arbiter import ResourceArbiter
from sylphy_entity.execution.worker_executor import SandboxWorkerExecutor
from sylphy_entity.runtime.process_manager import ProcessManager


@pytest.mark.asyncio
async def test_brain_proposal_routes_through_policy_and_verified_worker(sandbox):
    fact = WorldFact(
        subject="file:notes.txt",
        state=FactState.KNOWN,
        value={"relative_path": "notes.txt", "kind": "filesystem.file_created"},
        last_observation_id="observation-from-world",
        last_observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_id="filesystem:sandbox",
        confidence=0.99,
        verification="confirmed",
        permission_scope=f"sandbox:{sandbox.resolve()}",
    )
    world = WorldSnapshot(version=1, facts=(fact,))
    decision = EntityBrain().step(1.0, world, BrainState())

    assert len(decision.intent_proposals) == 1
    assert decision.intent_proposals[0].permission_class == "OBSERVE_SANDBOX"

    intent = IntentRouter().route(decision.intent_proposals[0])
    policy = PermissionPolicy(sandbox)
    process_manager = ProcessManager()
    worker = SandboxWorkerExecutor(process_manager)
    dispatcher = IntentDispatcher(
        permission_policy=policy,
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": worker},
    )
    try:
        result = await dispatcher.execute(intent)
        assert result.status is ExecutionStatus.SUCCEEDED
        assert result.actual_result["relative_path"] == "notes.txt"
        assert result.evidence_ids
        assert process_manager.snapshots() == []
    finally:
        await process_manager.close()
