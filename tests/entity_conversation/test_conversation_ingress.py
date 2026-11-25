from datetime import datetime

import pytest

from sylphy_entity.contracts.conversation import ConversationMessage
from sylphy_entity.contracts.events import EntityEvent
from sylphy_entity.interaction.conversation import ConversationIngress


class FakePublisher:
    def __init__(self):
        self.events = []

    async def publish_event(self, event: EntityEvent):
        self.events.append(event)


def test_message_contract_rejects_blank_or_oversized_input():
    with pytest.raises(ValueError, match="must not be blank"):
        ConversationMessage(text="  ", session_id="s1")
    with pytest.raises(ValueError, match="exceeds 16000"):
        ConversationMessage(text="x" * 16001, session_id="s1")
    with pytest.raises(ValueError, match="session_id"):
        ConversationMessage(text="hello", session_id=" ")


def test_message_contract_requires_timezone_aware_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        ConversationMessage(
            text="hello",
            session_id="s1",
            occurred_at=datetime(2026, 11, 24, 8, 0, 0),
        )


@pytest.mark.asyncio
async def test_ingress_publishes_one_user_message_as_one_entity_event():
    publisher = FakePublisher()
    ingress = ConversationIngress(publisher)
    message = ConversationMessage(
        text="Xin chào SYLPHY",
        session_id="session-01",
        message_id="message-01",
        channel="local-cli",
    )

    event = await ingress.submit(message)

    assert publisher.events == [event]
    assert event.event_id == "message-01"
    assert event.event_type == "conversation.message.received"
    assert event.source == "conversation"
    assert event.payload == {
        "session_id": "session-01",
        "message_id": "message-01",
        "role": "user",
        "channel": "local-cli",
        "text": "Xin chào SYLPHY",
    }


@pytest.mark.asyncio
async def test_ingress_does_not_create_reply_or_invoke_cognition():
    publisher = FakePublisher()
    ingress = ConversationIngress(publisher)

    result = await ingress.submit(
        ConversationMessage(text="What is your name?", session_id="session-02")
    )

    assert len(publisher.events) == 1
    assert result.event_type == "conversation.message.received"
    assert result.payload["role"] == "user"
    assert "assistant" not in result.payload
    assert "response" not in result.payload


@pytest.mark.asyncio
async def test_publisher_failure_is_propagated_without_claiming_acceptance():
    class FailingPublisher:
        async def publish_event(self, event):
            raise RuntimeError("event loop unavailable")

    ingress = ConversationIngress(FailingPublisher())
    with pytest.raises(RuntimeError, match="event loop unavailable"):
        await ingress.submit(
            ConversationMessage(text="hello", session_id="session-03")
        )


@pytest.mark.asyncio
async def test_message_is_durably_journaled_and_idempotent(tmp_path):
    from sylphy_entity.persistence.database import EntityDatabase
    from sylphy_entity.persistence.event_store import EventStore

    entity_db = EntityDatabase(tmp_path / "conversation.sqlite3")
    entity_db.open()

    class DurablePublisher:
        def __init__(self):
            self.store = EventStore(entity_db)

        async def publish_event(self, event):
            return self.store.append(event)

    ingress = ConversationIngress(DurablePublisher())
    message = ConversationMessage(
        text="Please remember this later",
        session_id="session-durable",
        message_id="stable-message-id",
    )

    first = await ingress.submit(message)
    second = await ingress.submit(message)

    assert first.event_id == second.event_id
    stored = EventStore(entity_db).read_after(0)
    assert len(stored) == 1
    assert stored[0].event.event_type == "conversation.message.received"
    assert stored[0].event.payload["text"] == "Please remember this later"
    entity_db.close()
