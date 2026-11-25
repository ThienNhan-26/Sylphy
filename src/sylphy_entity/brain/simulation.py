"""Virtual-time simulator for the pure Entity Brain."""

from __future__ import annotations

from dataclasses import dataclass

from sylphy_entity.brain import EntityBrain
from sylphy_entity.contracts.decision import BrainState, DecisionFrame, DriveInputs, GoalFeedback
from sylphy_entity.contracts.world import WorldSnapshot


@dataclass(frozen=True, slots=True)
class SimulationTick:
    elapsed: float
    world: WorldSnapshot
    frame: DecisionFrame


def simulate(
    brain: EntityBrain,
    initial_state: BrainState,
    worlds: tuple[WorldSnapshot, ...],
    *,
    dt: float = 1.0,
    takeovers: tuple[bool, ...] | None = None,
    drive_inputs: tuple[DriveInputs, ...] | None = None,
    feedback: tuple[tuple[GoalFeedback, ...], ...] | None = None,
) -> tuple[BrainState, tuple[SimulationTick, ...]]:
    """Run deterministic ticks without wall-clock time, I/O, or model calls."""
    state = initial_state
    trace: list[SimulationTick] = []
    for index, world in enumerate(worlds):
        takeover = takeovers[index] if takeovers and index < len(takeovers) else False
        inputs = drive_inputs[index] if drive_inputs and index < len(drive_inputs) else None
        outcomes = feedback[index] if feedback and index < len(feedback) else ()
        frame = brain.step(
            dt,
            world,
            state,
            external_takeover=takeover,
            drive_inputs=inputs,
            feedback=outcomes,
        )
        state = frame.next_state
        trace.append(SimulationTick(state.virtual_time, world, frame))
    return state, tuple(trace)
