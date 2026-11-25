from __future__ import annotations

from pathlib import Path
import pytest

from sylphy_entity.contracts.decision import IntentProposal
from sylphy_entity.contracts.execution import IntentOrigin, PermissionClass, ResourceMode, ResourceRequirement
from sylphy_entity.execution.intent_router import IntentRouter


@pytest.fixture

def sandbox(tmp_path: Path) -> Path:
    (tmp_path / "notes.txt").write_text("SYLPHY sandbox note", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.txt").write_text("nested content", encoding="utf-8")
    return tmp_path


def make_proposal(
    relative_path: str = "notes.txt",
    *,
    capability: str = "inspect_file_metadata",
    permission: str = "OBSERVE_SANDBOX",
    scope: str = "sandbox:/tmp/sandbox",
    intent_id: str = "intent-test-1",
    required_resources: tuple[str, ...] | None = None,
) -> IntentProposal:
    return IntentProposal(
        intent_id=intent_id,
        goal_id="goal-test-1",
        capability=capability,
        target=relative_path,
        arguments=(("relative_path", relative_path),),
        reason="test proposal",
        permission_class=permission,
        permission_scope=scope,
        required_resources=required_resources or (f"filesystem:sandbox:{relative_path}",),
        expected_outcome="test evidence",
        priority=0.7,
    )


def make_intent(
    relative_path: str = "notes.txt",
    *,
    capability: str = "inspect_file_metadata",
    permission: PermissionClass = PermissionClass.OBSERVE_SANDBOX,
    scope: str,
    intent_id: str = "intent-test-1",
    origin: IntentOrigin = IntentOrigin.AUTONOMOUS,
    resources: tuple[ResourceRequirement, ...] | None = None,
    timeout: float = 5.0,
):
    import time
    from sylphy_entity.contracts.execution import CancellationPolicy, Intent

    return Intent(
        intent_id=intent_id,
        goal_id="goal-test-1",
        capability=capability,
        target=relative_path,
        arguments=(("relative_path", relative_path),),
        reason="test Intent",
        preconditions=("target_exists", "target_is_regular_file"),
        permission_class=permission,
        permission_scope=scope,
        resources=resources or (
            ResourceRequirement(f"filesystem:{scope}:{relative_path}", ResourceMode.SHARED),
        ),
        priority=0.7,
        expected_outcome="test evidence",
        idempotency_key=intent_id,
        created_at_monotonic=time.monotonic(),
        deadline_at_monotonic=None,
        timeout_seconds=timeout,
        cancellation_policy=CancellationPolicy.CANCEL_ON_TAKEOVER,
        origin=origin,
    )


@pytest.fixture

def intent_factory():
    return make_intent


@pytest.fixture

def proposal_factory():
    return make_proposal


@pytest.fixture

def router():
    return IntentRouter()
