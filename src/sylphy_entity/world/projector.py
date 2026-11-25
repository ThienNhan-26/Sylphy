"""Translate adapter observations into the authoritative World Model."""

from __future__ import annotations

from sylphy_entity.contracts.observation import Observation
from .model import WorldModel


class WorldProjector:
    def __init__(self, model: WorldModel) -> None:
        self.model = model

    def apply(self, observation: Observation) -> bool:
        return self.model.apply(observation)
