"""Explicit transition policy for the Entity behaviour state machine."""

from __future__ import annotations

from sylphy_entity.contracts.decision import Behaviour


def choose_behaviour(
    *,
    external_takeover: bool,
    has_candidate: bool,
    active_goal: bool,
    waiting_goal: bool,
    world_changed: bool,
    recovering: bool = False,
    feedback_received: bool = False,
) -> Behaviour:
    if recovering:
        return Behaviour.RECOVERING
    if external_takeover:
        return Behaviour.YIELDING
    if feedback_received:
        return Behaviour.REFLECTING
    if has_candidate:
        return Behaviour.EVALUATING
    if active_goal:
        return Behaviour.PURSUING
    if waiting_goal:
        return Behaviour.WAITING
    if world_changed:
        return Behaviour.OBSERVING
    return Behaviour.RESTING
