import pytest

from sylphy_entity.world.adapters.simulated import SimulatedFilesystemAdapter


@pytest.mark.asyncio
async def test_simulated_adapter_emits_same_contract_event_kinds() -> None:
    observations = []

    async def sink(observation):
        observations.append(observation)

    adapter = SimulatedFilesystemAdapter(sink)
    await adapter.start()
    await adapter.discover("docs/existing.txt", size=2, modified_ns=5)
    await adapter.create("docs/a.txt", size=4, modified_ns=10)
    await adapter.modify("docs/a.txt", size=8, modified_ns=20)
    await adapter.rename("docs/a.txt", "docs/b.txt")
    await adapter.delete("docs/b.txt")
    await adapter.stop()

    assert [item.kind for item in observations] == [
        "filesystem.file_discovered",
        "filesystem.file_created",
        "filesystem.file_modified",
        "filesystem.file_renamed",
        "filesystem.file_deleted",
    ]
    assert [item.sequence for item in observations] == [1, 2, 3, 4, 5]
    assert observations[3].payload["old_relative_path"] == "docs/a.txt"


@pytest.mark.asyncio
async def test_simulated_adapter_rejects_path_escape() -> None:
    async def sink(_):
        return None

    adapter = SimulatedFilesystemAdapter(sink)
    await adapter.start()
    with pytest.raises(ValueError):
        await adapter.create("../outside.txt")
    with pytest.raises(ValueError):
        await adapter.create("C:/outside.txt")
    await adapter.stop()
