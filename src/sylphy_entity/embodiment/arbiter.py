"""Deterministic priority arbitration for Avatar behavior and body-region ownership."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping
from .contracts import AvatarBehavior, AvatarEvent, AvatarEventKind, BehaviorClaim, BodyRegion


DEFAULT_PRIORITIES: dict[AvatarBehavior, int] = {
    AvatarBehavior.IDLE: 10,
    AvatarBehavior.PROCESSING: 30,
    AvatarBehavior.REACTING: 50,
    AvatarBehavior.SPEAKING: 70,
    AvatarBehavior.INTERRUPTED: 90,
    AvatarBehavior.PAUSED: 100,
    AvatarBehavior.SHUTDOWN: 110,
}


@dataclass(frozen=True, slots=True)
class ArbitrationSnapshot:
    behavior: AvatarBehavior
    claims: tuple[BehaviorClaim, ...]
    region_owners: Mapping[BodyRegion, BehaviorClaim]


class BehaviorArbiter:
    """Presentation-only arbiter. It cannot mutate Entity state or execute actions."""
    def __init__(self, priorities: Mapping[AvatarBehavior, int] | None = None):
        self._priorities = dict(DEFAULT_PRIORITIES)
        if priorities:
            self._priorities.update(priorities)
        missing = set(AvatarBehavior) - set(self._priorities)
        if missing:
            raise ValueError(f"missing behavior priorities: {sorted(x.value for x in missing)}")
        self._claims: dict[str, BehaviorClaim] = {}
        self._seen_events: set[str] = set()
        self._last_voice_sequence: dict[str, int] = {}
        self._terminal = False

    def claim(self, claim: BehaviorClaim) -> ArbitrationSnapshot:
        if self._terminal:
            return self.snapshot()
        if claim.behavior is AvatarBehavior.IDLE:
            # IDLE is the no-claim fallback, not an override of active behavior.
            raise ValueError("IDLE is implicit and cannot preempt active behavior")
        if claim.priority is None:
            claim = BehaviorClaim(
                behavior=claim.behavior, owner=claim.owner, regions=claim.regions,
                priority=self._priorities[claim.behavior], session_id=claim.session_id,
                sequence=claim.sequence,
            )
        self._claims[claim.owner] = claim
        return self.snapshot()

    def release(self, owner: str) -> ArbitrationSnapshot:
        self._claims.pop(owner, None)
        return self.snapshot()

    def handle_event(self, event: AvatarEvent) -> ArbitrationSnapshot:
        if event.event_id in self._seen_events or self._terminal:
            return self.snapshot()
        self._seen_events.add(event.event_id)
        owner = self._owner_for(event)
        # Voice sequence numbers are scoped to a session. Guard every voice
        # event (not only terminal events), otherwise a delayed completion can
        # release a newer playback state for the same session.
        if event.kind.name.startswith("VOICE_") and event.session_id:
            last = self._last_voice_sequence.get(event.session_id, -1)
            if event.sequence <= last:
                return self.snapshot()
            self._last_voice_sequence[event.session_id] = event.sequence

        if event.kind is AvatarEventKind.ENTITY_STOPPED:
            self._terminal = True
            self._claims.clear()
            self._claims["system:shutdown"] = BehaviorClaim(
                AvatarBehavior.SHUTDOWN, "system:shutdown", tuple(BodyRegion),
                self._priorities[AvatarBehavior.SHUTDOWN], sequence=event.sequence,
            )
        elif event.kind is AvatarEventKind.ENTITY_PAUSED:
            # Pause overlays existing claims rather than destroying them.
            # Resume can then reveal the still-valid underlying presentation.
            self._claims["system:paused"] = BehaviorClaim(
                AvatarBehavior.PAUSED, "system:paused", tuple(BodyRegion),
                self._priorities[AvatarBehavior.PAUSED], sequence=event.sequence,
            )
        elif event.kind is AvatarEventKind.ENTITY_RESUMED:
            self._claims.pop("system:paused", None)
        elif event.kind is AvatarEventKind.COGNITION_STARTED:
            self.release("voice:interrupted")
            self._release_prefix("conversation:")
            self.claim(BehaviorClaim(AvatarBehavior.PROCESSING, owner,
                (BodyRegion.BODY, BodyRegion.HEAD, BodyRegion.EYES), session_id=event.session_id,
                sequence=event.sequence))
        elif event.kind is AvatarEventKind.COGNITION_COMPLETED:
            self.release(owner)
        elif event.kind is AvatarEventKind.CONVERSATION_MESSAGE_RECEIVED:
            self.release("voice:interrupted")
            self._release_prefix("conversation:")
            self.claim(BehaviorClaim(AvatarBehavior.REACTING, owner,
                (BodyRegion.HEAD, BodyRegion.EYES, BodyRegion.FACE), session_id=event.session_id,
                sequence=event.sequence))
        elif event.kind is AvatarEventKind.VOICE_PLAYBACK_STARTED:
            self.release("voice:interrupted")
            self._release_prefix("conversation:")
            self.claim(BehaviorClaim(AvatarBehavior.SPEAKING, owner,
                (BodyRegion.HEAD, BodyRegion.FACE, BodyRegion.MOUTH), session_id=event.session_id,
                sequence=event.sequence))
        elif event.kind in {AvatarEventKind.VOICE_PLAYBACK_COMPLETED,
                           AvatarEventKind.VOICE_PLAYBACK_INTERRUPTED,
                           AvatarEventKind.VOICE_PLAYBACK_FAILED}:
            self.release(owner)
            if event.kind is AvatarEventKind.VOICE_PLAYBACK_INTERRUPTED:
                self.claim(BehaviorClaim(AvatarBehavior.INTERRUPTED, "voice:interrupted",
                    (BodyRegion.HEAD, BodyRegion.FACE, BodyRegion.MOUTH), sequence=event.sequence))
        return self.snapshot()

    def _release_prefix(self, prefix: str) -> None:
        for owner in tuple(self._claims):
            if owner.startswith(prefix):
                self._claims.pop(owner, None)

    @staticmethod
    def _owner_for(event: AvatarEvent) -> str:
        if event.kind.name.startswith("VOICE_"):
            return f"voice:{event.session_id or 'unknown'}"
        if event.kind.name.startswith("COGNITION_"):
            return f"cognition:{event.session_id or 'default'}"
        if event.kind is AvatarEventKind.CONVERSATION_MESSAGE_RECEIVED:
            return f"conversation:{event.session_id or event.event_id}"
        return f"event:{event.source}:{event.session_id or 'default'}"

    def snapshot(self) -> ArbitrationSnapshot:
        winners: dict[BodyRegion, BehaviorClaim] = {}
        for claim in self._claims.values():
            priority = claim.priority if claim.priority is not None else self._priorities[claim.behavior]
            for region in claim.regions:
                current = winners.get(region)
                current_priority = -1 if current is None else (
                    current.priority if current.priority is not None else self._priorities[current.behavior]
                )
                if current is None or (priority, claim.sequence, claim.owner) > (
                    current_priority, current.sequence, current.owner
                ):
                    winners[region] = claim
        active = list(self._claims.values())
        if not active:
            behavior = AvatarBehavior.IDLE
        else:
            winner = max(active, key=lambda c: (
                c.priority if c.priority is not None else self._priorities[c.behavior],
                c.sequence, c.owner,
            ))
            behavior = winner.behavior
        return ArbitrationSnapshot(behavior, tuple(sorted(active, key=lambda c: c.owner)), winners)
