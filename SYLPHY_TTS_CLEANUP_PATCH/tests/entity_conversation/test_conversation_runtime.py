import asyncio

import pytest

from sylphy_entity.contracts.conversation import ConversationMessage
from sylphy_entity.contracts.events import EntityEvent
from sylphy_entity.interaction.conversation_runtime import ConversationRuntime
from sylphy_entity.interaction.generator import ConversationTurn
from sylphy_entity.persistence.database import EntityDatabase
from sylphy_entity.persistence.event_store import EventStore


class Publisher:
    def __init__(self, store):
        self.store = store
        self.events = []

    async def publish_event(self, event):
        self.events.append(event)
        self.store.append(event)


class Generator:
    def __init__(self, response="Hello from Sylphy"):
        self.response = response
        self.received = []
        self.closed = False

    async def generate(self, *, messages, timeout_seconds=90):
        self.received.append(messages)
        return self.response

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_conversation_runtime_generates_and_journals_response(tmp_path):
    db = EntityDatabase(tmp_path / "conversation-runtime.sqlite3")
    db.open()
    store = EventStore(db)
    publisher = Publisher(store)
    generator = Generator()
    runtime = ConversationRuntime(publisher=publisher, event_store=store, generator=generator)

    accepted = await runtime.submit(ConversationMessage(text="Xin chào", session_id="s1", message_id="m1"))
    assert accepted["accepted"] is True
    for _ in range(100):
        if not runtime.status()["active_generations"]:
            break
        await asyncio.sleep(0.01)

    events = [item.event for item in store.read_after(0)]
    assert [event.event_type for event in events] == [
        "conversation.message.received",
        "conversation.response.started",
        "conversation.response.completed",
    ]
    assert events[-1].payload["text"] == "Hello from Sylphy"
    assert generator.received[0][-1] == ConversationTurn("user", "Xin chào")
    await runtime.stop()
    assert generator.closed
    db.close()


@pytest.mark.asyncio
async def test_restart_rebuilds_completed_session_and_marks_inflight_interrupted(tmp_path):
    db = EntityDatabase(tmp_path / "conversation-recovery.sqlite3")
    db.open()
    store = EventStore(db)
    publisher = Publisher(store)
    await publisher.publish_event(EntityEvent(event_type="conversation.message.received", source="conversation", payload={"session_id": "s1", "message_id": "m1", "role": "user", "text": "Remember this"}))
    await publisher.publish_event(EntityEvent(event_type="conversation.response.started", source="conversation-runtime", payload={"session_id": "s1", "message_id": "m1"}))
    await publisher.publish_event(EntityEvent(event_type="conversation.message.received", source="conversation", payload={"session_id": "s2", "message_id": "m2", "role": "user", "text": "Hi"}))
    await publisher.publish_event(EntityEvent(event_type="conversation.response.started", source="conversation-runtime", payload={"session_id": "s2", "message_id": "m2"}))
    await publisher.publish_event(EntityEvent(event_type="conversation.response.completed", source="conversation-runtime", payload={"session_id": "s2", "message_id": "m2", "text": "Hello"}))

    runtime = ConversationRuntime(publisher=publisher, event_store=store, generator=Generator())
    assert await runtime.recover_interrupted() == 1
    assert runtime._sessions["s2"] == [ConversationTurn("user", "Hi"), ConversationTurn("assistant", "Hello")]
    assert publisher.events[-1].event_type == "conversation.response.interrupted"
    await runtime.stop()
    db.close()

@pytest.mark.asyncio
async def test_duplicate_submission_does_not_start_second_generation(tmp_path):
    db = EntityDatabase(tmp_path / "conversation-idempotency.sqlite3")
    db.open()
    store = EventStore(db)
    publisher = Publisher(store)
    generator = Generator()
    runtime = ConversationRuntime(publisher=publisher, event_store=store, generator=generator)
    message = ConversationMessage(text="Hello", session_id="s1", message_id="stable-id")
    first = await runtime.submit(message)
    second = await runtime.submit(message)
    assert first["accepted"] is True
    assert second["duplicate"] is True
    for _ in range(100):
        if not runtime.status()["active_generations"]:
            break
        await asyncio.sleep(0.01)
    assert len(generator.received) == 1
    await runtime.stop()
    db.close()


class BlockingVoice:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = False

    async def speak(self, session):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.asyncio
async def test_voice_playback_tasks_are_owned_and_drained_on_shutdown(tmp_path):
    db = EntityDatabase(tmp_path / "conversation-voice-lifecycle.sqlite3")
    db.open()
    store = EventStore(db)
    publisher = Publisher(store)
    voice = BlockingVoice()
    runtime = ConversationRuntime(
        publisher=publisher, event_store=store, generator=Generator(), voice=voice,
    )

    await runtime.submit(ConversationMessage(text="Speak this", session_id="s1", message_id="voice-1"))
    await asyncio.wait_for(voice.started.wait(), timeout=1.0)
    assert runtime.status()["active_voice_sessions"] == 1

    await runtime.stop()
    assert voice.cancelled is True
    assert runtime.status()["active_voice_sessions"] == 0
    assert runtime.status()["active_generations"] == 0
    db.close()

@pytest.mark.asyncio
async def test_voice_uses_clean_speech_copy_but_display_response_stays_formatted(tmp_path):
    class CapturingVoice:
        def __init__(self):
            self.session = None
            self.started = asyncio.Event()

        async def speak(self, session):
            self.session = session
            self.started.set()

    db = EntityDatabase(tmp_path / "conversation-speech-copy.sqlite3")
    db.open()
    store = EventStore(db)
    publisher = Publisher(store)
    voice = CapturingVoice()
    generator = Generator("Your favorite color is **violet** 🌸 — lovely choice!")
    runtime = ConversationRuntime(
        publisher=publisher, event_store=store, generator=generator, voice=voice,
    )
    await runtime.submit(ConversationMessage(text="What is my favorite color?", session_id="s1", message_id="speech-1"))
    await asyncio.wait_for(voice.started.wait(), timeout=1.0)

    completed = next(item.event for item in store.read_after(0)
                     if item.event.event_type == "conversation.response.completed")
    assert completed.payload["text"] == "Your favorite color is **violet** 🌸 — lovely choice!"
    assert voice.session.text == "Your favorite color is violet — lovely choice!"
    await runtime.stop()
    db.close()
