"""Presentation contracts and deterministic Avatar behavior arbitration."""
from .contracts import AvatarBehavior, AvatarEvent, AvatarEventKind, BodyRegion, BehaviorClaim
from .arbiter import BehaviorArbiter, ArbitrationSnapshot
from .adapter import to_avatar_event
from .presentation import (AnimationAction, AnimationCommand, PresentationFrame, BehaviorAnimationMapper, AnimationRenderer, PresentationRuntime)

__all__ = [
    "AvatarBehavior", "AvatarEvent", "AvatarEventKind", "BodyRegion",
    "BehaviorClaim", "BehaviorArbiter", "ArbitrationSnapshot", "to_avatar_event",
    "AnimationAction", "AnimationCommand", "PresentationFrame", "BehaviorAnimationMapper",
    "AnimationRenderer", "PresentationRuntime",
]
