from dataclasses import FrozenInstanceError
from datetime import datetime
from threading import enumerate as threads

import pytest

from sylphy.presence import PresenceEvent, PresenceEventType, PresenceService
from sylphy.runtime import Runtime


def event(title="Reminder", content="A short internal notice.", **kwargs):
    return PresenceEvent(PresenceEventType.NOTICE, title, content, **kwargs)


def test_presence_event_contract_validates_and_excludes_actions_paths_and_urls():
    valid = event(source_id="memory", deduplication_key="item-1")
    assert valid.event_id and valid.created_at.tzinfo is not None
    assert valid.event_type is PresenceEventType.NOTICE
    for title, content in [(" ", "notice"), ("title", " ")]:
        with pytest.raises(ValueError):
            event(title, content)
    with pytest.raises(ValueError, match="title exceeds"):
        event("x" * 121, "notice")
    with pytest.raises(ValueError, match="content exceeds"):
        event("title", "x" * 1001)
    with pytest.raises(ValueError, match="event_type"):
        PresenceEvent("notice", "title", "content")
    with pytest.raises(ValueError, match="URLs or filesystem paths"):
        event("title", "Open https://example.invalid")
    with pytest.raises(ValueError, match="URLs or filesystem paths"):
        event("title", r"Open C:\private\file.txt")
    with pytest.raises(ValueError, match="source_id"):
        event(source_id=r"C:\private")
    with pytest.raises(ValueError, match="timezone-aware"):
        event(created_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="control characters"):
        event("title", "spoof\x1b[31mterminal")
    with pytest.raises(ValueError, match="event_id"):
        event(event_id="../outside")
    with pytest.raises(TypeError):
        PresenceEvent(PresenceEventType.NOTICE, "title", "content", action="run")
    with pytest.raises(ValueError, match="capacity"):
        PresenceService(enabled=True, capacity=True)


def test_presence_disabled_by_default_and_enablement_suppresses_immediately():
    service = PresenceService()
    item = event()
    assert not service.enabled
    service.start()
    assert not service.publish(item)
    assert service.pending() == ()

    service.set_enabled(True)
    assert service.publish(item)
    service.set_enabled(False)
    assert not service.publish(event("another", "notice"))
    assert service.drain_pending() == ()
    assert service.pending() == (item,)
    service.set_enabled(True)
    assert service.drain_pending() == (item,)


def test_presence_deduplicates_only_while_matching_event_is_pending():
    service = PresenceService(enabled=True)
    service.start()
    first = event(source_id="memory", deduplication_key="same")
    duplicate = event("Updated title", "Different text", source_id="memory", deduplication_key="same")
    other_source = event(source_id="conversation", deduplication_key="same")
    assert service.publish(first)
    assert not service.publish(duplicate)
    assert service.publish(other_source)
    assert service.drain_pending() == (first, other_source)
    assert service.publish(duplicate)


def test_presence_queue_is_bounded_fifo_and_inspection_is_immutable():
    service = PresenceService(enabled=True, capacity=2)
    service.start()
    first, second, overflow = event("first", "one"), event("second", "two"), event("third", "three")
    assert service.publish(first) and service.publish(second)
    assert not service.publish(overflow)  # Full queue rejects the newest item.
    pending = service.pending()
    assert pending == (first, second)
    with pytest.raises(FrozenInstanceError):
        pending[0].title = "mutated"
    with pytest.raises(AttributeError):
        pending.append(overflow)
    assert service.drain_pending() == (first, second)
    assert service.pending() == ()


def test_dismiss_and_runtime_lifecycle_need_no_presence_thread():
    runtime = Runtime("model", heartbeat_enabled=False, presence_enabled=True)
    assert runtime.presence.enabled and not runtime.presence.started
    runtime.start()
    item = event()
    assert runtime.publish_presence(item)
    assert runtime.presence.dismiss(item.event_id)
    assert not runtime.presence.dismiss(item.event_id)
    assert not any(thread.name.startswith("sylphy-presence") for thread in threads())
    runtime.shutdown()
    assert not runtime.presence.started
    assert not runtime.publish_presence(event())


def test_runtime_presence_is_disabled_by_default():
    runtime = Runtime("model", heartbeat_enabled=False)
    assert not runtime.presence.enabled
    runtime.start()
    assert not runtime.publish_presence(event())
    runtime.shutdown()
