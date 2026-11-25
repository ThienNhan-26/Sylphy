import asyncio
from pathlib import Path

import pytest

from sylphy_entity.world.adapters.filesystem import FilesystemAdapter


async def wait_for_kind(observations, kind, subject=None, timeout=3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        for item in observations:
            if item.kind == kind and (subject is None or item.subject == subject):
                return item
        await asyncio.sleep(0.03)
    raise AssertionError(f"Did not observe {kind} for {subject}; got {[x.kind for x in observations]}")


@pytest.mark.asyncio
async def test_filesystem_adapter_discovers_creates_modifies_renames_and_deletes(tmp_path: Path) -> None:
    existing = tmp_path / "existing.txt"
    existing.write_text("secret contents must not enter observations", encoding="utf-8")
    observations = []

    async def sink(observation):
        observations.append(observation)

    adapter = FilesystemAdapter(tmp_path, sink, reconcile_interval_seconds=0.1, event_debounce_seconds=0)
    await adapter.start()
    try:
        discovered = await wait_for_kind(observations, "filesystem.file_discovered", "file:existing.txt")
        assert "secret contents" not in str(discovered.payload)
        assert discovered.permission_scope.startswith("sandbox:")

        created = tmp_path / "new.txt"
        created.write_text("abc", encoding="utf-8")
        await wait_for_kind(observations, "filesystem.file_created", "file:new.txt")

        created.write_text("a longer value", encoding="utf-8")
        await wait_for_kind(observations, "filesystem.file_modified", "file:new.txt")

        renamed = tmp_path / "renamed.txt"
        created.rename(renamed)
        move = await wait_for_kind(observations, "filesystem.file_renamed", "file:renamed.txt")
        assert move.payload["old_relative_path"] == "new.txt"

        renamed.unlink()
        await wait_for_kind(observations, "filesystem.file_deleted", "file:renamed.txt")
    finally:
        await adapter.stop()
    assert adapter.status()["running"] is False


@pytest.mark.asyncio
async def test_filesystem_adapter_rejects_missing_or_non_directory_root(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    async def sink(_):
        return None

    adapter = FilesystemAdapter(missing, sink)
    with pytest.raises(FileNotFoundError):
        await adapter.start()


@pytest.mark.asyncio
async def test_filesystem_adapter_skips_symlinks_outside_sandbox(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = sandbox / "linked.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not supported in this environment")

    observations = []

    async def sink(observation):
        observations.append(observation)

    adapter = FilesystemAdapter(sandbox, sink, reconcile_interval_seconds=0.1)
    await adapter.start()
    try:
        await asyncio.sleep(0.15)
        assert not any(item.subject == "file:linked.txt" for item in observations)
    finally:
        await adapter.stop()

@pytest.mark.asyncio
async def test_inaccessible_subtree_is_marked_stale_once_then_restored(tmp_path: Path, monkeypatch) -> None:
    from sylphy_entity.world.adapters.filesystem import _FileEntry

    observations = []

    async def sink(observation):
        observations.append(observation)

    adapter = FilesystemAdapter(tmp_path, sink, reconcile_interval_seconds=0.1)
    adapter._root = tmp_path.resolve()
    adapter._initialized = True
    entry = _FileEntry("blocked/a.txt", 5, 10, 1, 2)
    adapter._known = {"blocked/a.txt": entry}

    monkeypatch.setattr(adapter, "_collect_snapshot", lambda: ({}, ["blocked"]))
    await adapter._scan_once()
    await adapter._scan_once()
    assert [item.kind for item in observations] == ["filesystem.file_unavailable"]

    monkeypatch.setattr(adapter, "_collect_snapshot", lambda: ({"blocked/a.txt": entry}, []))
    await adapter._scan_once()
    assert [item.kind for item in observations] == [
        "filesystem.file_unavailable",
        "filesystem.file_restored",
    ]
