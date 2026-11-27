"""Replaceable, optional cognition capabilities for the Entity Runtime."""

from .interface import CognitionInterface
from .llamacpp import LlamaCppCognition
from .ollama import OllamaCognition

__all__ = ["CognitionInterface", "LlamaCppCognition", "OllamaCognition"]
