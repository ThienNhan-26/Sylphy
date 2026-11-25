import asyncio
from pathlib import Path

import pytest

from sylphy_entity.runtime.supervisor import EntitySupervisor
from sylphy_entity.contracts.world import FactState


@pytest.mark.asyncio
async def test_supervisor_exposes_world_snapshot_for_authorized_sandbox(tmp_path: Path) -> None:
    sandbox = tmp_path / "approved"
    sandbox.mkdir()
    initial = sandbox / "initial.txt"
    initial.write_text("initial", encoding="utf-8")
    supervisor = EntitySupervisor(
        lock_path=tmp_path / "runtime" / "entity.lock",
        port=0,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.1,
    )
    await supervisor.start()
    try:
        deadline = asyncio.get_running_loop().time() + 2.0
        while asyncio.get_running_loop().time() < deadline:
            snapshot = supervisor.world_snapshot()
            if any(fact["subject"] == "file:initial.txt" for fact in snapshot["facts"]):
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("initial sandbox file was not projected into World Model")

        created = sandbox / "created.txt"
        created.write_text("new", encoding="utf-8")
        deadline = asyncio.get_running_loop().time() + 2.0
        while asyncio.get_running_loop().time() < deadline:
            snapshot = supervisor.world_snapshot()
            fact = next((f for f in snapshot["facts"] if f["subject"] == "file:created.txt"), None)
            if fact and fact["state"] == FactState.KNOWN.value:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("created file was not projected")
    finally:
        await supervisor.shutdown()
