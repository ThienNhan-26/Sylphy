import asyncio

import httpx
import pytest

from sylphy_entity.runtime.lifecycle import LifecycleState
from sylphy_entity.runtime.supervisor import EntitySupervisor


@pytest.mark.asyncio
async def test_authenticated_shutdown_endpoint_stops_daemon_after_response(tmp_path) -> None:
    supervisor = EntitySupervisor(lock_path=tmp_path / "daemon.lock", port=0)
    await supervisor.start()
    base_url = f"http://127.0.0.1:{supervisor.api_port}"
    token = (tmp_path / "control.token").read_text(encoding="utf-8")

    async with httpx.AsyncClient(base_url=base_url, timeout=2.0) as client:
        denied = await client.post("/shutdown")
        assert denied.status_code == 401
        response = await client.post(
            "/shutdown", headers={"X-Sylphy-Control-Token": token}
        )
        assert response.status_code == 202
        assert response.json()["status"] == "shutdown_requested"

    for _ in range(100):
        if supervisor.lifecycle is LifecycleState.STOPPED:
            break
        await asyncio.sleep(0.02)
    assert supervisor.lifecycle is LifecycleState.STOPPED
    assert not (tmp_path / "control.token").exists()
