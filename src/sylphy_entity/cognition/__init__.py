"""Replaceable, optional cognition capabilities for the Entity Runtime."""

from .interface import CognitionInterface
from .ollama import OllamaCognition

__all__ = ["CognitionInterface", "OllamaCognition"]
