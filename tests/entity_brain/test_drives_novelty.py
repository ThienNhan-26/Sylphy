import pytest

from sylphy_entity.brain.novelty import novelty_score, record_visit
from sylphy_entity.brain.drives import update_drives
from sylphy_entity.contracts.decision import DriveInputs, DriveState


def test_novelty_unseen_is_maximum():
    assert novelty_score("x", (), 10) == 1.0


def test_novelty_recovers_over_time_with_frequency_and_depth_penalty():
    history = record_visit((), "x", 0.0)
    assert novelty_score("x", history, 0.0) == 0.0
    assert novelty_score("x", history, 3600.0) == pytest.approx(0.45454545)


def test_repeated_visits_and_depth_reduce_novelty():
    once = record_visit((), "x", 0.0)
    repeated = record_visit(once, "x", 10.0, depth_increment=2)
    assert repeated[0].visit_count == 2
    assert repeated[0].depth == 3
    assert novelty_score("x", repeated, 3600.0) < novelty_score("x", once, 3600.0)


def test_visit_history_is_bounded_and_subject_unique():
    history = record_visit((), "x", 1.0)
    history = record_visit(history, "y", 2.0)
    history = record_visit(history, "x", 3.0)
    assert len(history) == 2
    assert [item.subject for item in history] == ["y", "x"]
    assert history[-1].visit_count == 2


def test_drives_remain_bounded_and_inputs_are_explicit():
    state = DriveState()
    updated = update_drives(
        state,
        dt=300,
        inputs=DriveInputs(
            curiosity=1.0,
            continuity=1.0,
            social_responsiveness=1.0,
            self_maintenance=1.0,
            resource_pressure=1.0,
        ),
    )
    assert all(0.0 <= getattr(updated, name) <= 1.0 for name in (
        "curiosity", "continuity", "social_responsiveness", "self_maintenance", "resource_conservation"
    ))
    assert updated.curiosity > state.curiosity
    assert updated.self_maintenance > state.self_maintenance
    assert updated.resource_conservation > state.resource_conservation


def test_drive_inputs_reject_out_of_range_values():
    with pytest.raises(ValueError):
        DriveInputs(resource_pressure=1.5)
