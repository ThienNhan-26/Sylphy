from sylphy_entity.persistence.runtime_store import RuntimeStore


def test_runtime_store_detects_unclean_and_clean_previous_shutdown(tmp_path) -> None:
    database = tmp_path / "runtime.sqlite3"

    first = RuntimeStore(database)
    first_info = first.begin_runtime()
    assert first_info.boot_count == 1
    assert first_info.previous_shutdown_clean is None
    first.close()  # Simulate an abrupt exit: no clean-shutdown marker.

    second = RuntimeStore(database)
    second_info = second.begin_runtime()
    assert second_info.boot_count == 2
    assert second_info.previous_shutdown_clean is False
    second.mark_clean_shutdown()
    second.close()

    third = RuntimeStore(database)
    third_info = third.begin_runtime()
    try:
        assert third_info.boot_count == 3
        assert third_info.previous_shutdown_clean is True
        assert third_info.previous_shutdown_at is not None
    finally:
        third.mark_clean_shutdown()
        third.close()
