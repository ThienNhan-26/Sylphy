import asyncio
import sys

import httpx
import pytest

from sylphy_entity.runtime.lifecycle import LifecycleState
from sylphy_entity.runtime.supervisor import EntitySupervisor


@pytest.mark.asyncio
async def test_daemon_api_events_worker_failure_and_clean_shutdown(tmp_path) -> None:
    supervisor = EntitySupervisor(
        lock_path=tmp_path / "entity.lock",
        port=0,
        tick_interval_seconds=0.05,
    )
    await supervisor.start()
    assert supervisor.lifecycle is LifecycleState.RUNNING
    base_url = f"http://127.0.0.1:{supervisor.api_port}"

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=2.0) as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json()["lifecycle"] == "running"

            unauthorized = await client.post(
                "/events",
                json={"event_type": "test.signal", "source": "pytest", "payload": {"value": 3}},
            )
            assert unauthorized.status_code == 401
            control_token = (tmp_path / "control.token").read_text(encoding="utf-8")
            invalid = await client.post(
                "/events",
                headers={"X-Sylphy-Control-Token": control_token},
                json={"event_type": "   ", "source": "pytest", "payload": {}},
            )
            assert invalid.status_code == 422
            accepted = await client.post(
                "/events",
                headers={"X-Sylphy-Control-Token": control_token},
                json={"event_type": "test.signal", "source": "pytest", "payload": {"value": 3}},
            )
            assert accepted.status_code == 202
            for _ in range(50):
                status = (await client.get("/status")).json()
                if status["events"]["processed"] == 1:
                    break
                await asyncio.sleep(0.01)
            assert status["events"]["processed"] == 1
            assert status["scheduler"]["running"] is True

            await supervisor.start_worker(
                "test-failure",
                [sys.executable, "-c", "import sys; sys.exit(9)"],
            )
            for _ in range(100):
                if supervisor.lifecycle is LifecycleState.DEGRADED:
                    break
                await asyncio.sleep(0.01)
            assert supervisor.lifecycle is LifecycleState.DEGRADED

            # A failed worker degrades the Entity but does not take down its API.
            status_after_worker = await client.get("/status")
            assert status_after_worker.status_code == 200
            assert status_after_worker.json()["lifecycle"] == "degraded"
    finally:
        await supervisor.shutdown()

    assert supervisor.lifecycle is LifecycleState.STOPPED
    assert not supervisor._event_loop.running


@pytest.mark.asyncio
async def test_conversation_event_is_projected_to_world_without_copying_message_text(tmp_path) -> None:
    supervisor = EntitySupervisor(
        lock_path=tmp_path / "entity.lock",
        port=0,
        tick_interval_seconds=0.05,
    )
    await supervisor.start()
    base_url = f"http://127.0.0.1:{supervisor.api_port}"
    try:
        control_token = (tmp_path / "control.token").read_text(encoding="utf-8")
        async with httpx.AsyncClient(base_url=base_url, timeout=2.0) as client:
            accepted = await client.post(
                "/events",
                headers={"X-Sylphy-Control-Token": control_token},
                json={
                    "event_type": "conversation.message.received",
                    "source": "conversation",
                    "payload": {
                        "session_id": "session-world-01",
                        "message_id": "message-world-01",
                        "role": "user",
                        "channel": "local-cli",
                        "text": "private conversation content",
                    },
                },
            )
            assert accepted.status_code == 202
            event_id = accepted.json()["event_id"]
            for _ in range(50):
                world = (await client.get("/world")).json()
                if event_id in world["recent_events"]:
                    break
                await asyncio.sleep(0.01)
            assert event_id in world["recent_events"]
            assert "private conversation content" not in str(world)
            assert world["facts"] == []
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_memory_status_api_is_read_only_bounded_and_exposes_provenance_metadata(tmp_path) -> None:
    supervisor = EntitySupervisor(
        lock_path=tmp_path / "entity.lock",
        port=0,
        tick_interval_seconds=0.05,
    )
    await supervisor.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{supervisor.api_port}", timeout=2.0) as client:
            response = await client.get("/memory/status?limit=10")
            assert response.status_code == 200
            payload = response.json()
            assert payload["limit"] == 10
            assert payload["active"] == []
            assert payload["pending"] == []
            invalid = await client.get("/memory/status?limit=101")
            assert invalid.status_code == 422
    finally:
        await supervisor.shutdown()
