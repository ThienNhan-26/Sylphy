"""Translate authoritative EntityEvent facts into presentation-only AvatarEvents."""
from __future__ import annotations
from sylphy_entity.contracts.events import EntityEvent
from .contracts import AvatarEvent, AvatarEventKind

_EVENT_MAP = {kind.value: kind for kind in AvatarEventKind}


def to_avatar_event(event: EntityEvent) -> AvatarEvent | None:
    """Return None for events outside Avatar's presentation contract."""
    kind = _EVENT_MAP.get(event.event_type)
    if kind is None:
        return None
    payload = dict(event.payload)
    session_id = payload.get("session_id")
    sequence = payload.get("sequence", 0)
    if not isinstance(session_id, str):
        session_id = None
    if not isinstance(sequence, int) or sequence < 0:
        sequence = 0
    return AvatarEvent(
        kind=kind,
        source=event.source,
        event_id=event.event_id,
        session_id=session_id,
        sequence=sequence,
        occurred_at=event.occurred_at,
        payload=payload,
    )
