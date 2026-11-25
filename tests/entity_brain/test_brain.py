from dataclasses import replace
from datetime import datetime, timezone

import pytest

from sylphy_entity.brain import EntityBrain
from sylphy_entity.contracts.decision import (
    Behaviour,
    BrainState,
    DriveInputs,
    Goal,
    GoalFeedback,
    GoalOutcome,
    GoalStatus,
)
from sylphy_entity.contracts.world import FactState, WorldFact, WorldSnapshot


def world_with_file(version=1, subject="file:notes.txt", **payload):
    value = {
        "relative_path": subject.removeprefix("file:"),
        "kind": "filesystem.file_created",
    }
    value.update(payload)
    fact = WorldFact(
        subject=subject,
        state=FactState.KNOWN,
        value=value,
        last_observation_id="obs-1",
        last_observed_at=datetime.now(timezone.utc),
        source_id="filesystem:sandbox",
        confidence=0.95,
        verification="confirmed",
        permission_scope="sandbox:C:/test-sandbox",
    )
    return WorldSnapshot(version=version, facts=(fact,))


def test_brain_proposes_goal_and_non_executable_intent_for_novel_known_file():
    frame = EntityBrain().step(1.0, world_with_file(), BrainState())
    assert frame.behaviour is Behaviour.EVALUATING
    assert len(frame.goal_proposals) == 1
    assert frame.goal_proposals[0].kind == "observe_file_change"
    assert len(frame.intent_proposals) == 1
    assert frame.intent_proposals[0].capability == "inspect_file_metadata"
    assert frame.intent_proposals[0].permission_class == "OBSERVE_SANDBOX"
    assert frame.next_state.goals[0].status is GoalStatus.ACTIVE
    assert frame.next_state.tick_count == 1


def test_decisions_are_deterministic_for_same_input():
    brain = EntityBrain()
    state = BrainState()
    world = world_with_file()
    assert brain.step(1.0, world, state) == brain.step(1.0, world, state)


def test_user_takeover_suspends_goal_and_does_not_propose_intent():
    brain = EntityBrain()
    first = brain.step(1.0, world_with_file(), BrainState())
    second = brain.step(1.0, world_with_file(), first.next_state, external_takeover=True)
    assert second.behaviour is Behaviour.YIELDING
    assert second.should_yield
    assert second.goal_proposals == ()
    assert second.intent_proposals == ()
    assert second.next_state.yielded
    assert second.next_state.goals[0].status is GoalStatus.SUSPENDED


def test_unknown_or_stale_fact_cannot_propose_goal():
    fact = WorldFact(
        subject="file:notes.txt",
        state=FactState.STALE,
        value={"relative_path": "notes.txt", "kind": "filesystem.file_created"},
        last_observation_id="obs-stale",
        last_observed_at=datetime.now(timezone.utc),
        reason="inaccessible",
    )
    world = WorldSnapshot(version=1, facts=(fact,))
    frame = EntityBrain().step(1.0, world, BrainState())
    assert frame.goal_proposals == ()
    assert frame.intent_proposals == ()
    assert frame.behaviour is Behaviour.OBSERVING


def test_duplicate_active_goal_not_reproposed():
    brain = EntityBrain()
    first = brain.step(1.0, world_with_file(), BrainState())
    second = brain.step(1.0, world_with_file(), first.next_state)
    assert second.goal_proposals == ()
    assert second.intent_proposals == ()
    assert len(second.active_goal_ids) == 1
    assert second.behaviour is Behaviour.PURSUING


def test_resume_reactivates_goal_shifts_deadline_and_resets_transient_latches():
    brain = EntityBrain()
    first = brain.step(1.0, world_with_file(), BrainState())
    goal = replace(first.next_state.goals[0], deadline_at_virtual=100.0)
    state = replace(first.next_state, goals=(goal,), transient_latches=("debounce", "pending_transition"))
    yielded = brain.step(10.0, world_with_file(), state, external_takeover=True)
    assert yielded.next_state.goals[0].status is GoalStatus.SUSPENDED
    resumed = brain.step(20.0, world_with_file(), yielded.next_state)
    goal_after = resumed.next_state.goals[0]
    assert resumed.behaviour is Behaviour.PURSUING
    assert goal_after.status is GoalStatus.ACTIVE
    assert goal_after.deadline_at_virtual == pytest.approx(120.0)
    assert resumed.next_state.transient_latches == ()
    assert resumed.next_state.novelty == first.next_state.novelty


def test_failed_goal_uses_exponential_cooldown_and_can_retry_after_novelty_recovers():
    brain = EntityBrain()
    first = brain.step(1.0, world_with_file(), BrainState())
    goal = first.next_state.goals[0]
    failed = brain.step(
        1.0,
        world_with_file(),
        first.next_state,
        feedback=(GoalFeedback(goal.goal_id, GoalOutcome.FAILED, reason="temporary failure"),),
    )
    failed_goal = failed.next_state.goals[0]
    assert failed_goal.status is GoalStatus.FAILED
    assert failed_goal.failure_count == 1
    assert failed_goal.cooldown_until_virtual == pytest.approx(32.0)

    during_cooldown = brain.step(10.0, world_with_file(), failed.next_state)
    assert during_cooldown.goal_proposals == ()

    state = during_cooldown.next_state
    retry = None
    # dt is intentionally capped at 300 seconds/tick. Let novelty recover
    # through multiple deterministic virtual ticks rather than one giant step.
    for _ in range(8):
        retry = brain.step(300.0, world_with_file(), state)
        state = retry.next_state
        if retry.goal_proposals:
            break
    assert retry is not None and len(retry.goal_proposals) == 1
    assert retry.next_state.goals[0].status is GoalStatus.ACTIVE
    assert retry.next_state.goals[0].failure_count == 1


def test_success_requires_evidence_and_completes_goal():
    with pytest.raises(ValueError, match="evidence_id"):
        GoalFeedback("goal-1", GoalOutcome.SUCCEEDED)

    brain = EntityBrain()
    first = brain.step(1.0, world_with_file(), BrainState())
    goal_id = first.next_state.goals[0].goal_id
    completed = brain.step(
        1.0,
        WorldSnapshot(version=2),
        first.next_state,
        feedback=(GoalFeedback(goal_id, GoalOutcome.SUCCEEDED, evidence_id="evidence-123"),),
    )
    assert completed.behaviour is Behaviour.REFLECTING
    assert completed.next_state.goals[0].status is GoalStatus.COMPLETED
    assert completed.next_state.goals[0].completion_evidence == ("evidence-123",)


def test_unsafe_relative_paths_never_become_goal_or_intent():
    for path in ("../secret.txt", "C:/Windows/win.ini", "/etc/passwd", ""):
        frame = EntityBrain().step(1.0, world_with_file(payload_path="unused", relative_path=path), BrainState())
        assert frame.goal_proposals == ()
        assert frame.intent_proposals == ()


def test_recovery_state_suppresses_new_goals():
    frame = EntityBrain().step(1.0, world_with_file(), BrainState(), recovering=True)
    assert frame.behaviour is Behaviour.RECOVERING
    assert frame.goal_proposals == ()
    assert frame.intent_proposals == ()


def test_negative_dt_is_clamped_and_tick_advances():
    frame = EntityBrain().step(-5.0, WorldSnapshot(version=0), BrainState())
    assert frame.next_state.virtual_time == 0.0
    assert frame.next_state.tick_count == 1


def test_non_finite_dt_is_rejected():
    with pytest.raises(ValueError, match="finite"):
        EntityBrain().step(float("nan"), WorldSnapshot(version=0), BrainState())


def test_priority_prefers_low_cost_low_risk_well_evidenced_facts():
    now = datetime.now(timezone.utc)
    strong = WorldFact(
        subject="file:strong.txt",
        state=FactState.KNOWN,
        value={"relative_path": "strong.txt", "kind": "filesystem.file_created", "estimated_cost": 0.1, "risk": 0.05},
        last_observation_id="strong-obs",
        last_observed_at=now,
        source_id="filesystem:sandbox",
        confidence=0.95,
        verification="confirmed",
        permission_scope="sandbox:C:/safe",
    )
    weak = WorldFact(
        subject="file:weak.txt",
        state=FactState.KNOWN,
        value={"relative_path": "weak.txt", "kind": "filesystem.file_created", "estimated_cost": 0.9, "risk": 0.9},
        last_observation_id="weak-obs",
        last_observed_at=now,
        source_id="filesystem:sandbox",
        confidence=0.3,
        verification="unverified",
        permission_scope="sandbox:C:/safe",
    )
    frame = EntityBrain().step(1.0, WorldSnapshot(version=1, facts=(weak, strong)), BrainState())
    assert len(frame.goal_proposals) == 1
    assert frame.goal_proposals[0].subject == "file:strong.txt"


def test_drive_input_changes_same_tick_goal_priority():
    brain = EntityBrain()
    world = world_with_file()
    low = brain.step(1.0, world, BrainState(), drive_inputs=DriveInputs(curiosity=0.0))
    high = brain.step(1.0, world, BrainState(), drive_inputs=DriveInputs(curiosity=1.0))
    assert high.goal_proposals[0].priority > low.goal_proposals[0].priority


def test_waiting_goal_survives_takeover_and_resume_as_waiting():
    brain = EntityBrain()
    first = brain.step(1.0, world_with_file(), BrainState())
    goal_id = first.next_state.goals[0].goal_id
    waiting = brain.step(
        1.0,
        world_with_file(),
        first.next_state,
        feedback=(GoalFeedback(goal_id, GoalOutcome.WAITING, progress=0.4),),
    )
    assert waiting.next_state.goals[0].status is GoalStatus.WAITING
    assert waiting.next_state.goals[0].progress == pytest.approx(0.4)
    yielded = brain.step(5.0, world_with_file(), waiting.next_state, external_takeover=True)
    assert yielded.next_state.goals[0].status is GoalStatus.SUSPENDED
    resumed = brain.step(5.0, world_with_file(), yielded.next_state)
    assert resumed.next_state.goals[0].status is GoalStatus.WAITING
    assert resumed.behaviour is Behaviour.WAITING


def test_completed_goal_revisit_uses_new_intent_attempt_id():
    brain = EntityBrain()
    first = brain.step(1.0, world_with_file(), BrainState())
    goal = first.next_state.goals[0]
    completed = brain.step(
        1.0,
        WorldSnapshot(version=2),
        first.next_state,
        feedback=(GoalFeedback(goal.goal_id, GoalOutcome.SUCCEEDED, evidence_id="ev-1"),),
    )
    state = completed.next_state
    last = None
    for _ in range(20):
        last = brain.step(300.0, world_with_file(version=3), state)
        state = last.next_state
        if last.intent_proposals:
            break
    assert last is not None and last.intent_proposals
    assert last.intent_proposals[0].intent_id != first.intent_proposals[0].intent_id


def test_world_fact_outside_sandbox_scope_is_not_a_candidate():
    fact = WorldFact(
        subject="file:notes.txt",
        state=FactState.KNOWN,
        value={"relative_path": "notes.txt", "kind": "filesystem.file_created"},
        last_observation_id="obs-outside",
        last_observed_at=datetime.now(timezone.utc),
        source_id="external",
        confidence=1.0,
        verification="confirmed",
        permission_scope="filesystem:anywhere",
    )
    frame = EntityBrain().step(1.0, WorldSnapshot(version=1, facts=(fact,)), BrainState())
    assert frame.goal_proposals == ()
    assert frame.intent_proposals == ()


def test_resource_pressure_reduces_priority_of_costly_goal():
    brain = EntityBrain()
    world = world_with_file(estimated_cost=0.95)
    calm = brain.step(1.0, world, BrainState(), drive_inputs=DriveInputs(resource_pressure=0.0))
    pressured = brain.step(1.0, world, BrainState(), drive_inputs=DriveInputs(resource_pressure=1.0))
    assert pressured.goal_proposals[0].priority < calm.goal_proposals[0].priority


def test_second_failure_increases_cooldown_and_penalty():
    brain = EntityBrain()
    first = brain.step(1.0, world_with_file(), BrainState())
    goal_id = first.next_state.goals[0].goal_id
    failed_once = brain.step(
        1.0,
        world_with_file(),
        first.next_state,
        feedback=(GoalFeedback(goal_id, GoalOutcome.FAILED),),
    )
    state = failed_once.next_state
    attempt = None
    for _ in range(10):
        attempt = brain.step(300.0, world_with_file(), state)
        state = attempt.next_state
        if attempt.goal_proposals:
            break
    assert attempt is not None and attempt.goal_proposals
    retry_priority = attempt.goal_proposals[0].priority
    retry_id = attempt.next_state.goals[0].goal_id
    failed_twice = brain.step(
        1.0,
        world_with_file(),
        attempt.next_state,
        feedback=(GoalFeedback(retry_id, GoalOutcome.FAILED),),
    )
    second_record = failed_twice.next_state.goals[0]
    assert second_record.failure_count == 2
    assert second_record.cooldown_until_virtual - failed_twice.next_state.virtual_time == pytest.approx(60.0)
    assert second_record.status is GoalStatus.FAILED
    # History also records the retry; another attempt cannot happen immediately.
    assert brain.step(1.0, world_with_file(), failed_twice.next_state).goal_proposals == ()


def test_deadline_expiry_marks_goal_failed():
    brain = EntityBrain()
    first = brain.step(1.0, world_with_file(deadline_seconds=5.0), BrainState())
    goal_id = first.next_state.goals[0].goal_id
    expired = brain.step(10.0, WorldSnapshot(version=1), first.next_state)
    goal = next(goal for goal in expired.next_state.goals if goal.goal_id == goal_id)
    assert goal.status is GoalStatus.FAILED
    assert goal.failure_count == 1


def test_unverified_fact_receives_lower_priority_than_confirmed_fact():
    now = datetime.now(timezone.utc)
    confirmed = WorldFact(
        subject="file:confirmed.txt", state=FactState.KNOWN,
        value={"relative_path": "confirmed.txt", "kind": "filesystem.file_created"},
        last_observation_id="confirmed", last_observed_at=now,
        source_id="filesystem:sandbox", confidence=0.9, verification="confirmed",
        permission_scope="sandbox:C:/safe",
    )
    unverified = WorldFact(
        subject="file:unverified.txt", state=FactState.KNOWN,
        value={"relative_path": "unverified.txt", "kind": "filesystem.file_created"},
        last_observation_id="unverified", last_observed_at=now,
        source_id="filesystem:sandbox", confidence=0.4, verification="unverified",
        permission_scope="sandbox:C:/safe",
    )
    frame = EntityBrain().step(1.0, WorldSnapshot(version=1, facts=(unverified, confirmed)), BrainState())
    assert frame.goal_proposals[0].subject == "file:confirmed.txt"


def test_brain_state_and_nested_drive_state_are_immutable():
    from dataclasses import FrozenInstanceError

    state = BrainState()
    with pytest.raises(FrozenInstanceError):
        state.behaviour = Behaviour.PURSUING  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        state.drives.curiosity = 1.0  # type: ignore[misc]


def test_world_user_interaction_and_external_resource_ownership_trigger_yield():
    brain = EntityBrain()
    user_world = WorldSnapshot(version=1, user_interaction_active=True)
    user_frame = brain.step(1.0, user_world, BrainState())
    assert user_frame.behaviour is Behaviour.YIELDING
    assert user_frame.should_yield
    assert user_frame.next_state.yielded

    resource_world = WorldSnapshot(version=2, externally_owned_resources=("filesystem:sandbox:notes.txt",))
    resource_frame = brain.step(1.0, resource_world, BrainState())
    assert resource_frame.behaviour is Behaviour.YIELDING
    assert resource_frame.should_yield


def test_world_resource_pressure_updates_resource_conservation_drive():
    low = EntityBrain().step(1.0, WorldSnapshot(version=1, resource_pressure=0.0), BrainState())
    high = EntityBrain().step(1.0, WorldSnapshot(version=1, resource_pressure=1.0), BrainState())
    assert high.next_state.drives.resource_conservation > low.next_state.drives.resource_conservation


def test_brain_pursues_one_goal_at_a_time_and_defers_other_files():
    now = datetime.now(timezone.utc)
    facts = tuple(
        WorldFact(
            subject=f"file:{name}", state=FactState.KNOWN,
            value={"relative_path": name, "kind": "filesystem.file_created"},
            last_observation_id=f"obs-{index}", last_observed_at=now,
            source_id="filesystem:sandbox", confidence=0.95, verification="confirmed",
            permission_scope="sandbox:C:/safe",
        )
        for index, name in enumerate(("first.txt", "second.txt"), start=1)
    )
    brain = EntityBrain()
    first = brain.step(1.0, WorldSnapshot(version=1, facts=facts), BrainState())
    assert len(first.goal_proposals) == 1
    second = brain.step(1.0, WorldSnapshot(version=1, facts=facts), first.next_state)
    assert second.goal_proposals == ()
    assert len(second.active_goal_ids) == 1

    completed = brain.step(
        1.0,
        WorldSnapshot(version=1, facts=facts),
        second.next_state,
        feedback=(GoalFeedback(first.active_goal_ids[0], GoalOutcome.SUCCEEDED, evidence_id="read-evidence"),),
    )
    assert len(completed.goal_proposals) == 1
    assert completed.goal_proposals[0].subject != first.goal_proposals[0].subject
