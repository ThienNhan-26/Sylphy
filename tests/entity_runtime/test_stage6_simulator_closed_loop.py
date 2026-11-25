from __future__ import annotations
import pytest

from sylphy_entity.brain.core import EntityBrain
from sylphy_entity.world.adapters.simulated import SimulatedFilesystemAdapter
from sylphy_entity.contracts.observation import Observation
from sylphy_entity.contracts.decision import BrainState
from sylphy_entity.execution.intent_router import IntentRouter
from sylphy_entity.world import WorldModel, WorldProjector


def test_simulated_file_observation_produces_bounded_intent_proposal() -> None:
    model = WorldModel()
    projector = WorldProjector(model)
    observation = Observation(
        source_id="simulated:sandbox",
        subject="file:docs/proof.txt",
        kind="filesystem.file_created",
        payload={"relative_path": "docs/proof.txt", "size_bytes": 17, "modified_ns": 123},
        permission_scope="simulated:sandbox",
        verification="confirmed",
    )
    assert projector.apply(observation) is True
    frame = EntityBrain().step(0.1, model.snapshot(), BrainState())
    assert frame.goal_proposals
    assert frame.intent_proposals
    proposal = frame.intent_proposals[0]
    assert proposal.capability == "inspect_file_metadata"
    assert proposal.target == "docs/proof.txt"
    intent = IntentRouter().route(proposal)
    assert intent.permission_scope == "simulated:sandbox"
    assert intent.permission_class.value == "OBSERVE_SANDBOX"
    assert intent.origin.value == "autonomous"


@pytest.mark.asyncio
async def test_simulated_adapter_runs_full_brain_router_permission_worker_feedback_loop(tmp_path):
    import asyncio
    from sylphy_entity.contracts.decision import BrainState, GoalFeedback, GoalOutcome
    from sylphy_entity.contracts.execution import ExecutionStatus
    from sylphy_entity.execution.dispatcher import IntentDispatcher
    from sylphy_entity.execution.permissions import PermissionPolicy
    from sylphy_entity.execution.resource_arbiter import ResourceArbiter
    from sylphy_entity.execution.worker_executor import SandboxWorkerExecutor
    from sylphy_entity.runtime.process_manager import ProcessManager

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = sandbox / "simulated-proof.txt"
    target.write_text("simulator full-loop proof", encoding="utf-8")

    model = WorldModel()
    projector = WorldProjector(model)
    adapter = SimulatedFilesystemAdapter(
        lambda observation: asyncio.sleep(0, result=projector.apply(observation)),
        permission_scope=f"sandbox:{sandbox.resolve()}",
    )
    await adapter.start()
    await adapter.create("simulated-proof.txt", size=27, modified_ns=123)

    frame = EntityBrain().step(0.1, model.snapshot(), BrainState())
    assert frame.intent_proposals, "simulated observation must produce a Brain proposal"
    intent = IntentRouter().route(frame.intent_proposals[0])

    process_manager = ProcessManager()
    dispatcher = IntentDispatcher(
        permission_policy=PermissionPolicy(sandbox),
        resource_arbiter=ResourceArbiter(),
        handlers={"inspect_file_metadata": SandboxWorkerExecutor(process_manager)},
    )
    try:
        result = await dispatcher.execute(intent)
        assert result.status is ExecutionStatus.SUCCEEDED
        assert result.actual_result["relative_path"] == "simulated-proof.txt"
        assert result.evidence_ids
        feedback = GoalFeedback(
            goal_id=frame.intent_proposals[0].goal_id,
            outcome=GoalOutcome.SUCCEEDED,
            evidence_id=result.evidence_ids[0],
        )
        next_frame = EntityBrain().step(0.1, model.snapshot(), frame.next_state, feedback=(feedback,))
        assert next_frame.next_state.goals
        assert next_frame.next_state.goals[0].status.value in {"completed", "succeeded"}
    finally:
        await process_manager.close()
        await adapter.stop()
