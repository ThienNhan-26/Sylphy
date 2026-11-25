from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sylphy_entity.runtime.supervisor import EntitySupervisor


@pytest.mark.asyncio
async def test_live_sandbox_observation_runs_first_autonomous_closed_loop(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    target = sandbox / "proof.txt"
    target.write_text("stage6 read-only proof", encoding="utf-8")

    supervisor = EntitySupervisor(
        lock_path=runtime / "daemon.lock",
        port=0,
        tick_interval_seconds=0.1,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.1,
        autonomy_enabled=True,
    )
    await supervisor.start()
    try:
        for _ in range(100):
            status = supervisor.status()
            history = status["autonomy"]["history"]
            if history:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("autonomous loop did not execute within 5 seconds")

        assert status["autonomy"]["enabled"] is True
        assert history[0]["status"] == "succeeded"
        assert history[0]["capability"] == "inspect_file_metadata"
        assert history[0]["target"] == "proof.txt"
        assert history[0]["evidence_ids"]
        notice = status["autonomy"]["notices"][-1]
        assert notice["kind"] == "file_inspected"
        assert notice["status"] == "succeeded"
        assert status["brain"]["tick_count"] >= 1
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_autonomy_pause_prevents_new_autonomous_execution(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    runtime = tmp_path / "runtime"
    sandbox.mkdir()
    runtime.mkdir()
    supervisor = EntitySupervisor(
        lock_path=runtime / "daemon.lock",
        port=0,
        tick_interval_seconds=0.1,
        sandbox_root=sandbox,
        reconcile_interval_seconds=0.1,
    )
    await supervisor.start()
    try:
        result = await supervisor.pause_autonomy()
        assert result["paused"] is True
        (sandbox / "paused.txt").write_text("must not inspect while paused", encoding="utf-8")
        await asyncio.sleep(0.5)
        status = supervisor.status()
        assert status["autonomy"]["paused"] is True
        assert status["autonomy"]["history"] == []
        await supervisor.resume_autonomy()
        assert supervisor.status()["autonomy"]["paused"] is False
    finally:
        await supervisor.shutdown()
