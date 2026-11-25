"""Opt-in sensor adapter primitives. No hardware is opened by importing this package."""

from .camera import CameraAdapter, CapturedFrame, MetadataFrameProcessor, OpenCVCameraBackend
from .microphone import (
    AudioMetadataProcessor,
    CapturedAudio,
    MicrophoneAdapter,
    SoundDeviceMicrophoneBackend,
)
from .consent import ConsentDenied, SensorConsentLedger
from .session import SensorAdapterSession, SensorSessionState

__all__ = [
    "CameraAdapter",
    "CapturedFrame",
    "MetadataFrameProcessor",
    "OpenCVCameraBackend",
    "AudioMetadataProcessor",
    "CapturedAudio",
    "MicrophoneAdapter",
    "SoundDeviceMicrophoneBackend",
    "ConsentDenied",
    "SensorConsentLedger",
    "SensorAdapterSession",
    "SensorSessionState",
]
