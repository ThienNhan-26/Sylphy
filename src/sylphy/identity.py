"""Load the editable Identity v0 definition from TOML."""

from dataclasses import dataclass
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class Identity:
    name: str
    identity: str
    personality: str
    communication_style: str
    relationship: str
    boundaries: str
    version: str

    def to_prompt(self) -> str:
        return "\n".join((
            f"Name: {self.name}", f"Identity: {self.identity}", f"Personality: {self.personality}",
            f"Communication style: {self.communication_style}", f"Relationship: {self.relationship}",
            f"Boundaries: {self.boundaries}", f"Identity version: {self.version}",
        ))


def load_identity(path: Path) -> Identity:
    with path.open("rb") as file:
        data = tomllib.load(file).get("identity")
    if not isinstance(data, dict):
        raise ValueError("Identity config must contain an [identity] table")
    required = ("name", "identity", "personality", "communication_style", "relationship", "boundaries", "version")
    missing = [key for key in required if not isinstance(data.get(key), str) or not data[key].strip()]
    if missing:
        raise ValueError("Missing or empty identity fields: " + ", ".join(missing))
    return Identity(**{key: data[key].strip() for key in required})
