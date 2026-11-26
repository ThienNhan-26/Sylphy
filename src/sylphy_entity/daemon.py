"""Command-line entry point for the independent SYLPHY Entity Daemon."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sylphy_entity.config import load_entity_config
from sylphy_entity.runtime.instance_lock import InstanceAlreadyRunning
from sylphy_entity.runtime.paths import default_runtime_dir
from sylphy_entity.runtime.supervisor import EntitySupervisor
from sylphy_entity.interaction.generator import (
    ConversationGenerator,
    OllamaConversationGenerator,
    LlamaCppConversationGenerator,
)


async def _run(
    port: int, sandbox_root: str | None, reconcile_interval: float, autonomy: bool,
    cognition_model: str | None, cognition_base_url: str,
    voice_model: str | None, voice_voices: str | None, conversation_model: str | None,
    conversation_provider: str = "ollama", conversation_base_url: str | None = None,
    conversation_thinking: bool = True,
) -> None:
    runtime_dir = default_runtime_dir()
    cognition_provider = None
    if cognition_model:
        from sylphy_entity.cognition.ollama import OllamaCognition
        cognition_provider = OllamaCognition(model=cognition_model, base_url=cognition_base_url)
    conversation_generator = None
    if conversation_model:
        if conversation_provider == "llama.cpp":
            conversation_generator = LlamaCppConversationGenerator(
                model=conversation_model,
                base_url=conversation_base_url or "http://127.0.0.1:9931",
                thinking_enabled=conversation_thinking,
            )
        else:
            conversation_generator = OllamaConversationGenerator(
                model=conversation_model,
                base_url=conversation_base_url or "http://127.0.0.1:11434",
            )
    config = load_entity_config()
    supervisor = EntitySupervisor(
        lock_path=runtime_dir / "entity-daemon.lock",
        port=port,
        sandbox_root=Path(sandbox_root) if sandbox_root else None,
        reconcile_interval_seconds=reconcile_interval,
        autonomy_enabled=autonomy,
        cognition_provider=cognition_provider,
        cognition_model_name=cognition_model,
        conversation_generator=conversation_generator,
        identity_file=config.identity_file,
    )
    if voice_model and voice_voices:
        from sylphy_entity.voice.providers import KokoroOnnxProvider, SoundDeviceOutput
        supervisor.configure_voice_output(
            tts=KokoroOnnxProvider(voice_model, voice_voices),
            output=SoundDeviceOutput(),
        )
    await supervisor.start()
    try:
        await supervisor.serve_forever()
    finally:
        await supervisor.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SYLPHY Entity Daemon")
    parser.add_argument("--port", type=int, default=8765, help="Loopback API port (default: 8765)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--sandbox-root",
        help="Explicitly authorize one existing directory for read-only filesystem observation",
    )
    parser.add_argument(
        "--autonomy",
        action="store_true",
        help="Enable the Stage-6 read-only autonomous closed loop (requires --sandbox-root)",
    )
    parser.add_argument(
        "--cognition-model",
        help="Enable bounded local Ollama cognition (requires --autonomy and --sandbox-root)",
    )
    parser.add_argument(
        "--cognition-base-url", default="http://127.0.0.1:11434",
        help="Local Ollama URL (loopback only; default: http://127.0.0.1:11434)",
    )
    parser.add_argument("--conversation-model", help="Enable local conversational replies using Ollama (independent of autonomy/sandbox)")
    parser.add_argument(
        "--conversation-provider",
        choices=["ollama", "llama.cpp"],
        default="ollama",
        help="Conversation model provider (default: ollama)",
    )
    parser.add_argument(
        "--conversation-base-url",
        default=None,
        help="Base URL for the conversation provider (default: ollama http://127.0.0.1:11434 or llama.cpp http://127.0.0.1:9931)",
    )
    parser.add_argument(
        "--conversation-thinking",
        choices=["on", "off"],
        default="on",
        help="Enable Gemma thinking for the llama.cpp conversation provider (default: on)",
    )
    parser.add_argument("--voice-model", help="Path to local Kokoro ONNX model (enables Voice Output)")
    parser.add_argument("--voice-voices", help="Path to local Kokoro voices .bin file")
    parser.add_argument(
        "--reconcile-interval",
        type=float,
        default=2.0,
        help="Filesystem reconciliation interval in seconds (default: 2.0)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.autonomy and not args.sandbox_root:
            parser.error("--autonomy requires an explicit --sandbox-root")
        if args.cognition_model and (not args.autonomy or not args.sandbox_root):
            parser.error("--cognition-model requires --autonomy and --sandbox-root")
        if bool(args.voice_model) != bool(args.voice_voices):
            parser.error("--voice-model and --voice-voices must be supplied together")
        asyncio.run(_run(
            args.port, args.sandbox_root, args.reconcile_interval, args.autonomy,
            args.cognition_model, args.cognition_base_url,
            args.voice_model, args.voice_voices, args.conversation_model,
            args.conversation_provider, args.conversation_base_url,
            args.conversation_thinking == "on",
        ))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Shutdown requested")
    except InstanceAlreadyRunning as exc:
        print(f"SYLPHY Entity is already running: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
