from datetime import datetime, timezone
from sylphy_entity.contracts.events import EntityEvent
from sylphy_entity.embodiment import AvatarEventKind, to_avatar_event


def test_voice_event_maps_to_avatar_contract_without_mutating_entity_event():
    source=EntityEvent(event_type="voice.playback.started", source="voice-output",
        payload={"session_id":"voice-1", "sequence":4, "state":"playing"})
    adapted=to_avatar_event(source)
    assert adapted is not None
    assert adapted.kind is AvatarEventKind.VOICE_PLAYBACK_STARTED
    assert adapted.session_id == "voice-1"
    assert adapted.sequence == 4
    assert adapted.event_id == source.event_id
    assert source.event_type == "voice.playback.started"


def test_conversation_event_uses_actual_entity_event_name():
    source=EntityEvent(event_type="conversation.message.received", source="conversation")
    adapted=to_avatar_event(source)
    assert adapted is not None
    assert adapted.kind is AvatarEventKind.CONVERSATION_MESSAGE_RECEIVED


def test_unknown_entity_events_are_ignored_by_avatar_adapter():
    source=EntityEvent(event_type="world.observation", source="filesystem")
    assert to_avatar_event(source) is None
