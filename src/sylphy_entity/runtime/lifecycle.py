"""Explicit Entity lifecycle state machine."""

from __future__ import annotations

from enum import Enum


class LifecycleState(str, Enum):
    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


_ALLOWED_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.STARTING: frozenset(
        {LifecycleState.READY, LifecycleState.STOPPING, LifecycleState.ERROR}
    ),
    LifecycleState.READY: frozenset(
        {
            LifecycleState.RUNNING,
            LifecycleState.DEGRADED,
            LifecycleState.STOPPING,
            LifecycleState.ERROR,
        }
    ),
    LifecycleState.RUNNING: frozenset(
        {LifecycleState.DEGRADED, LifecycleState.STOPPING, LifecycleState.ERROR}
    ),
    LifecycleState.DEGRADED: frozenset(
        {LifecycleState.RUNNING, LifecycleState.STOPPING, LifecycleState.ERROR}
    ),
    LifecycleState.STOPPING: frozenset({LifecycleState.STOPPED, LifecycleState.ERROR}),
    LifecycleState.STOPPED: frozenset(),
    LifecycleState.ERROR: frozenset({LifecycleState.STOPPING, LifecycleState.STOPPED}),
}


class InvalidLifecycleTransition(RuntimeError):
    """Raised when the runtime attempts an illegal lifecycle transition."""


class Lifecycle:
    def __init__(self) -> None:
        self._state = LifecycleState.STARTING

    @property
    def state(self) -> LifecycleState:
        return self._state

    def transition(self, target: LifecycleState) -> None:
        if target == self._state:
            return
        if target not in _ALLOWED_TRANSITIONS[self._state]:
            raise InvalidLifecycleTransition(
                f"Cannot transition Entity lifecycle from {self._state.value} "
                f"to {target.value}"
            )
        self._state = target
