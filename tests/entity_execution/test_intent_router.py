import pytest

from sylphy_entity.contracts.execution import PermissionClass
from sylphy_entity.execution.intent_router import IntentRejected


def test_routes_brain_proposal_into_scoped_read_only_intent(proposal_factory, router):
    proposal = proposal_factory()
    intent = router.route(proposal)
    assert intent.intent_id == proposal.intent_id
    assert intent.capability == "inspect_file_metadata"
    assert intent.permission_class is PermissionClass.OBSERVE_SANDBOX
    assert intent.permission_scope == proposal.permission_scope
    assert intent.target == "notes.txt"
    assert intent.resources[0].resource_key.endswith(":notes.txt")
    assert intent.idempotency_key == proposal.intent_id


@pytest.mark.parametrize("path", ["../outside.txt", "a/../../outside", "/etc/passwd", r"C:\Windows\win.ini", "folder:stream.txt"])
def test_rejects_unsafe_target_paths(path, proposal_factory, router):
    proposal = proposal_factory(path, required_resources=(f"filesystem:sandbox:{path}",))
    with pytest.raises(IntentRejected):
        router.route(proposal)


def test_rejects_unknown_capability(proposal_factory, router):
    proposal = proposal_factory(capability="run_shell", required_resources=("filesystem:sandbox:notes.txt",))
    with pytest.raises(IntentRejected, match="not registered"):
        router.route(proposal)


def test_rejects_permission_escalation(proposal_factory, router):
    proposal = proposal_factory(permission="READ_SANDBOX")
    with pytest.raises(IntentRejected, match="does not match capability"):
        router.route(proposal)


def test_rejects_unscoped_resource_claim(proposal_factory, router):
    proposal = proposal_factory(required_resources=("filesystem:*",))
    with pytest.raises(IntentRejected, match="path-scoped"):
        router.route(proposal)


def test_simulator_scope_can_be_routed_but_is_not_live_authority(proposal_factory, router):
    proposal = proposal_factory(scope="simulated:sandbox")
    intent = router.route(proposal)
    assert intent.permission_scope == "simulated:sandbox"


def test_rejects_duplicate_and_unknown_proposal_arguments(proposal_factory, router):
    from dataclasses import replace

    base = proposal_factory()
    duplicate = replace(
        base,
        arguments=(("relative_path", "notes.txt"), ("relative_path", "other.txt")),
    )
    with pytest.raises(IntentRejected, match="duplicate argument"):
        router.route(duplicate)

    extra = replace(base, arguments=(("relative_path", "notes.txt"), ("mode", "write")))
    with pytest.raises(IntentRejected, match="only relative_path"):
        router.route(extra)
