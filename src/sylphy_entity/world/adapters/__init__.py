"""World sensor adapters. Adapters emit observations only; they never act."""

from .base import ObservationSink, WorldAdapter
from .filesystem import FilesystemAdapter
from .simulated import SimulatedFilesystemAdapter

__all__ = [
    "ObservationSink",
    "WorldAdapter",
    "FilesystemAdapter",
    "SimulatedFilesystemAdapter",
]
