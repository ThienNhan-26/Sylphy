import pytest

from sylphy_entity.runtime.instance_lock import InstanceAlreadyRunning
from sylphy_entity.runtime.lifecycle import LifecycleState
from sylphy_entity.runtime.supervisor import EntitySupervisor


@pytest.mark.asyncio
async def test_second_daemon_is_refused_and_clean_restart_is_detected(tmp_path) -> None:
    lock_path = tmp_path / "entity.lock"
    first = EntitySupervisor(lock_path=lock_path, port=0)
    second = EntitySupervisor(lock_path=lock_path, port=0)

    await first.start()
    with pytest.raises(InstanceAlreadyRunning):
        await second.start()
    # Failed startup of the duplicate must not remove the owner's token.
    assert (tmp_path / "control.token").exists()
    await first.shutdown()
    assert first.lifecycle is LifecycleState.STOPPED

    restarted = EntitySupervisor(lock_path=lock_path, port=0)
    await restarted.start()
    try:
        status = restarted.status()
        assert status["persistence"]["boot_count"] == 2
        assert status["persistence"]["previous_shutdown_clean"] is True
    finally:
        await restarted.shutdown()
