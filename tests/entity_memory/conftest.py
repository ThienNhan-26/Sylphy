from pathlib import Path
import pytest

from sylphy_entity.persistence.database import EntityDatabase
from sylphy_entity.persistence.runtime_store import RuntimeStore


@pytest.fixture
def entity_db(tmp_path: Path):
    database = EntityDatabase(tmp_path / "entity.sqlite3")
    runtime = RuntimeStore(database=database)
    runtime.begin_runtime()
    try:
        yield database
    finally:
        runtime.close()
        database.close()
