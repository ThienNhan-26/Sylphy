import pytest

from sylphy_entity.runtime.instance_lock import InstanceAlreadyRunning, InstanceLock


def test_instance_lock_prevents_second_daemon_and_releases(tmp_path) -> None:
    lock_path = tmp_path / "daemon.lock"
    first = InstanceLock(lock_path)
    second = InstanceLock(lock_path)

    first.acquire()
    try:
        with pytest.raises(InstanceAlreadyRunning):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    assert second.acquired
    second.release()
