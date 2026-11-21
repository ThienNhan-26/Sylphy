from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sylphy.context import ContextBuilder
from sylphy.identity import load_identity
from sylphy.interaction_policy import (
    InteractionAction, InteractionEvent, InteractionPolicy, InteractionReason, PolicyConditions,
)


def event(source="exploration", expires_at=None):
    now = datetime.now(timezone.utc)
    return InteractionEvent("event-1", source, now, "question-1", "presence", "A grounded question.",
                            expires_at or now + timedelta(minutes=5))


def test_policy_contract_supports_all_five_actions_with_provenance():
    policy = InteractionPolicy()
    cases = [
        (PolicyConditions(), InteractionAction.SPEAK, InteractionReason.ELIGIBLE_TO_SHARE),
        (PolicyConditions(needs_context=True), InteractionAction.EXPLORE,
         InteractionReason.NEEDS_MORE_CONTEXT),
        (PolicyConditions(cooldown_active=True, can_wait=True), InteractionAction.WAIT,
         InteractionReason.COOLDOWN_ACTIVE),
        (PolicyConditions(request_permission=True, permission_granted=False),
         InteractionAction.REQUEST_PERMISSION, InteractionReason.PERMISSION_REQUIRED),
        (PolicyConditions(feature_enabled=False), InteractionAction.SILENCE,
         InteractionReason.FEATURE_DISABLED),
    ]
    for conditions, action, reason in cases:
        result = policy.decide(event(), conditions)
        assert (result.action, result.reason_code) == (action, reason)
        assert result.source_event_id == "event-1"
        assert result.dedup_key == "question-1"
    ask = policy.decide(event(), cases[3][0])
    assert ask.required_permission == "presence"
    assert policy.decide(event(), cases[0][0]).message == "A grounded question."


@pytest.mark.parametrize(
    "conditions,reason",
    [
        (PolicyConditions(permission_denied=True, request_permission=True, permission_granted=False),
         InteractionReason.PERMISSION_DENIED),
        (PolicyConditions(dismissed=True), InteractionReason.DISMISSED_BY_USER),
        (PolicyConditions(duplicate=True), InteractionReason.DUPLICATE_EVENT),
        (PolicyConditions(queue_available=False), InteractionReason.QUEUE_FULL),
        (PolicyConditions(resource_available=False), InteractionReason.RESOURCE_UNAVAILABLE),
        (PolicyConditions(grounded=False), InteractionReason.INSUFFICIENT_CONFIDENCE),
        (PolicyConditions(relevant=False), InteractionReason.NOT_RELEVANT_NOW),
    ],
)
def test_policy_hard_gates_select_silence(conditions, reason):
    result = InteractionPolicy().decide(event(), conditions)
    assert result.action is InteractionAction.SILENCE
    assert result.reason_code is reason
    assert result.message is None


def test_policy_rejects_unknown_sources_and_expired_events():
    policy = InteractionPolicy()
    assert policy.decide(event("vision"), PolicyConditions()).reason_code is InteractionReason.INVALID_SOURCE
    expired = event(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    result = policy.decide(expired, PolicyConditions())
    assert (result.action, result.reason_code) == (InteractionAction.SILENCE, InteractionReason.EXPIRED_EVENT)
    with pytest.raises(ValueError, match="timezone-aware"):
        InteractionEvent("e", "exploration", datetime.now(), "d", "none", "payload")


def test_identity_prompt_is_loaded_from_configuration_and_separate_from_policy():
    identity = load_identity(Path("identity.toml"))
    prompt = identity.to_prompt()
    assert identity.version == "1.0"
    assert "curious and willing to initiate" in prompt
    assert "Respect privacy and explicit permissions" in prompt
    assert "SPEAK" not in prompt and "reason_code" not in prompt
    context = ContextBuilder("system baseline", prompt, 4000).build([], "hello")
    assert context[0] == {"role": "system", "content": "system baseline"}
    assert context[1] == {"role": "system", "content": prompt}
    assert context[2] == {"role": "user", "content": "hello"}
