import httpx
import pytest

from sylphy_entity.contracts.observation import Observation
from sylphy_entity.interface.local_api import create_app
from sylphy_entity.world.model import WorldModel


class RuntimeStub:
    def __init__(self):
        self.model = WorldModel()

    def status(self):
        return {"lifecycle": "running"}

    def world_snapshot(self):
        return self.model.snapshot().model_dump(mode="json")

    async def publish_event(self, event):
        return None

    async def request_shutdown(self):
        return None


@pytest.mark.asyncio
async def test_world_endpoint_exposes_immutable_snapshot_contract() -> None:
    runtime = RuntimeStub()
    app = create_app(runtime, "secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/world")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 0
    assert body["facts"] == []
    assert "captured_at" in body

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        spoof = await client.post(
            "/events",
            headers={"X-Sylphy-Control-Token": "secret"},
            json={"event_type": "world.observation", "source": "client", "payload": {}},
        )
    assert spoof.status_code == 422
