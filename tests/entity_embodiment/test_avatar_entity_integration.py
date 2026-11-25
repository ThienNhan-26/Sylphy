from __future__ import annotations

from pathlib import Path

import pytest

from sylphy_entity.contracts.events import EntityEvent
from sylphy_entity.runtime.supervisor import EntitySupervisor


@pytest.mark.asyncio
async def test_entity_voice_events_drive_read_only_avatar_presentation(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    supervisor = EntitySupervisor(lock_path=runtime / "daemon.lock", port=0)
    await supervisor.start()
    try:
        initial = supervisor.avatar_presentation_status()
        assert initial["behavior"] == "idle"

        await supervisor.publish_event(EntityEvent(
            event_type="voice.playback.started",
            source="voice-output",
            payload={"session_id": "session-a", "sequence": 1},
        ))
        for _ in range(50):
            speaking = supervisor.avatar_presentation_status()
            if speaking["behavior"] == "speaking":
                break
            import asyncio
            await asyncio.sleep(0.01)

        assert speaking["behavior"] == "speaking"
        assert any(command["region"] == "mouth" for command in speaking["commands"])

        await supervisor.publish_event(EntityEvent(
            event_type="voice.playback.completed",
            source="voice-output",
            payload={"session_id": "session-a", "sequence": 2},
        ))
        for _ in range(50):
            completed = supervisor.avatar_presentation_status()
            if completed["behavior"] == "idle":
                break
            await asyncio.sleep(0.01)

        assert completed["behavior"] == "idle"
        assert completed["revision"] > speaking["revision"]
    finally:
        await supervisor.shutdown()


def test_avatar_presentation_status_returns_defensive_command_copies(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    supervisor = EntitySupervisor(lock_path=runtime / "daemon.lock", port=0)
    first = supervisor.avatar_presentation_status()
    first["commands"].append({"clip": "untrusted"})
    assert supervisor.avatar_presentation_status()["commands"] == []
