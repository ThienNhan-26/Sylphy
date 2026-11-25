from datetime import datetime, timezone

from sylphy_entity.brain import EntityBrain
from sylphy_entity.brain.simulation import simulate
from sylphy_entity.contracts.decision import BrainState, DriveInputs, GoalFeedback, GoalOutcome, GoalStatus
from sylphy_entity.contracts.world import FactState, WorldFact, WorldSnapshot


def world_with_one_file(version: int) -> WorldSnapshot:
    fact = WorldFact(
        subject="file:simulation/notes.txt",
        state=FactState.KNOWN,
        value={"relative_path": "simulation/notes.txt", "kind": "filesystem.file_created"},
        last_observation_id="simulation-observation-1",
        last_observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_id="filesystem:sandbox",
        confidence=0.95,
        verification="confirmed",
        permission_scope="sandbox:simulation",
    )
    return WorldSnapshot(version=version, facts=(fact,))


def test_long_virtual_entity_simulation_is_deterministic_and_preserves_goal():
    worlds = tuple(world_with_one_file(1) for _ in range(1000))
    takeovers = tuple(100 <= tick < 120 for tick in range(1000))
    a_state, a_trace = simulate(
        EntityBrain(), BrainState(), worlds, dt=5.0, takeovers=takeovers
    )
    b_state, b_trace = simulate(
        EntityBrain(), BrainState(), worlds, dt=5.0, takeovers=takeovers
    )
    assert a_state == b_state
    assert [tick.frame for tick in a_trace] == [tick.frame for tick in b_trace]
    assert a_state.tick_count == 1000
    assert a_state.virtual_time == 5000.0
    assert len(a_state.goals) == 1
    assert a_state.goals[0].status is GoalStatus.ACTIVE
    assert any(tick.frame.should_yield for tick in a_trace)
    assert a_trace[99].frame.active_goal_ids == a_trace[120].frame.active_goal_ids


def test_simulation_supports_takeover_feedback_and_drives():
    worlds = tuple(world_with_one_file(i + 1) for i in range(4))
    final, trace = simulate(
        EntityBrain(),
        BrainState(),
        worlds,
        takeovers=(False, True, False, False),
        drive_inputs=(
            DriveInputs(),
            DriveInputs(social_responsiveness=0.7),
            DriveInputs(),
            DriveInputs(),
        ),
    )
    assert trace[0].frame.intent_proposals
    assert trace[1].frame.should_yield
    assert not trace[2].frame.should_yield
    assert final.tick_count == 4


def test_simulation_applies_feedback_to_goal_state():
    worlds = (world_with_one_file(1), world_with_one_file(1), WorldSnapshot(version=2))
    first_goal = simulate(EntityBrain(), BrainState(), worlds[:1])[0].goals[0]
    final, trace = simulate(
        EntityBrain(),
        BrainState(),
        worlds,
        feedback=((), (), (GoalFeedback(first_goal.goal_id, GoalOutcome.SUCCEEDED, evidence_id="sim-evidence"),)),
    )
    assert trace[-1].frame.behaviour.value == "reflecting"
    assert final.goals[0].status is GoalStatus.COMPLETED


def test_seeded_long_run_preserves_brain_invariants_under_mixed_world_events():
    import random

    rng = random.Random(20261123)
    brain = EntityBrain()
    state = BrainState()
    evidence_counter = 0

    for tick in range(5000):
        facts = []
        for index in range(12):
            status_roll = rng.random()
            status = (
                FactState.KNOWN if status_roll > 0.12
                else FactState.STALE if status_roll > 0.06
                else FactState.UNKNOWN
            )
            facts.append(
                WorldFact(
                    subject=f"file:stress/file-{index}.txt",
                    state=status,
                    value={
                        "relative_path": f"stress/file-{index}.txt",
                        "kind": "filesystem.file_modified",
                        "relevance": rng.random(),
                        "risk": rng.random() * 0.25,
                        "estimated_cost": rng.random(),
                    },
                    last_observation_id=f"stress-observation-{tick}-{index}",
                    last_observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    source_id="filesystem:stress-sandbox",
                    confidence=rng.random(),
                    verification=rng.choice(("confirmed", "inferred", "unverified")),
                    permission_scope="sandbox:stress",
                )
            )

        active_goals = [goal for goal in state.goals if goal.status is GoalStatus.ACTIVE]
        feedback = ()
        if active_goals and rng.random() < 0.08:
            goal = active_goals[0]
            outcome = rng.choice((GoalOutcome.SUCCEEDED, GoalOutcome.FAILED, GoalOutcome.WAITING))
            evidence_counter += 1
            feedback = (
                GoalFeedback(
                    goal_id=goal.goal_id,
                    outcome=outcome,
                    evidence_id=f"stress-evidence-{evidence_counter}" if outcome is GoalOutcome.SUCCEEDED else None,
                    progress=rng.random() if outcome is GoalOutcome.WAITING else None,
                ),
            )

        external_owned = ("filesystem:stress",) if rng.random() < 0.015 else ()
        world = WorldSnapshot(
            version=tick + 1,
            facts=tuple(facts),
            user_interaction_active=rng.random() < 0.02,
            resource_pressure=rng.random(),
            externally_owned_resources=external_owned,
        )
        frame = brain.step(
            rng.choice((0.1, 1.0, 5.0, 30.0)),
            world,
            state,
            external_takeover=rng.random() < 0.01,
            drive_inputs=DriveInputs(
                curiosity=rng.random(),
                continuity=rng.random(),
                social_responsiveness=rng.random(),
                self_maintenance=rng.random(),
                resource_pressure=rng.random(),
            ),
            feedback=feedback,
        )
        state = frame.next_state

        values = (
            state.drives.curiosity,
            state.drives.continuity,
            state.drives.social_responsiveness,
            state.drives.self_maintenance,
            state.drives.resource_conservation,
        )
        assert all(0.0 <= value <= 1.0 for value in values)
        assert sum(goal.status is GoalStatus.ACTIVE for goal in state.goals) <= 1
        assert len(state.goals) <= 512
        if frame.should_yield:
            assert frame.intent_proposals == ()
        assert all(intent.permission_scope.startswith("sandbox:") for intent in frame.intent_proposals)
        assert all(".." not in intent.target.replace("\\", "/").split("/") for intent in frame.intent_proposals)

    assert state.tick_count == 5000
    assert state.virtual_time > 0.0
