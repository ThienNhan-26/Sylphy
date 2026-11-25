import pytest

from sylphy_entity.runtime.lifecycle import (
    InvalidLifecycleTransition,
    Lifecycle,
    LifecycleState,
)


def test_lifecycle_moves_through_valid_runtime_states() -> None:
    lifecycle = Lifecycle()
    lifecycle.transition(LifecycleState.READY)
    lifecycle.transition(LifecycleState.RUNNING)
    lifecycle.transition(LifecycleState.DEGRADED)
    lifecycle.transition(LifecycleState.RUNNING)
    lifecycle.transition(LifecycleState.STOPPING)
    lifecycle.transition(LifecycleState.STOPPED)
    assert lifecycle.state is LifecycleState.STOPPED


def test_lifecycle_rejects_invalid_transition() -> None:
    lifecycle = Lifecycle()
    with pytest.raises(InvalidLifecycleTransition):
        lifecycle.transition(LifecycleState.RUNNING)
