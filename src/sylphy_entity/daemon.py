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

# One authoritative model identity for Conversation and Cognition. It is the
# Gemma 4 Artemis llama.cpp model already used by the Conversation adapter.
DEFAULT_LLAMACPP_MODEL = "gemma-4-E2B-it-Artemis-Q3_K_M.gguf"
DEFAULT_LLAMACPP_URL = "http://127.0.0.1:9931"
DEFAULT_LEGACY_OLLAMA_URL = "http://127.0.0.1:11434"


async def _run(
    port: int, sandbox_root: str | None, reconcile_interval: float, autonomy: bool,
    conversation_model: str | None, conversation_provider: str,
    conversation_base_url: str | None, conversation_thinking: bool,
    cognition_provider: str, cognition_model: str | None,
    cognition_base_url: str | None,
    voice_model: str | None, voice_voices: str | None,
) -> None:
    runtime_dir = default_runtime_dir()

    # In the intended runtime both capabilities talk to one loopback llama.cpp
    # server process, so they share one inference slot (concurrency limit 1,
    # matching the existing per-adapter limit and [resources] in config.toml).
    llamacpp_slot = asyncio.Semaphore(1)
    shared_llamacpp_url = conversation_base_url or DEFAULT_LLAMACPP_URL

    conversation_generator: ConversationGenerator | None = None
    if conversation_model:
        if conversation_provider == "llama.cpp":
            conversation_generator = LlamaCppConversationGenerator(
                model=conversation_model,
                base_url=shared_llamacpp_url,
                thinking_enabled=conversation_thinking,
                slot=llamacpp_slot,
            )
        else:
            # Legacy-only selection. Not used by the intended runtime.
            conversation_generator = OllamaConversationGenerator(
                model=conversation_model,
                base_url=conversation_base_url or DEFAULT_LEGACY_OLLAMA_URL,
            )

    cognition_provider_instance = None
    cognition_model_name: str | None = None
    # Cognition inherits the authoritative Conversation model; --cognition-model
    # remains only as an explicit legacy override, never a second identity.
    effective_cognition_model = cognition_model or conversation_model
    if effective_cognition_model and autonomy and sandbox_root:
        if cognition_provider == "llama.cpp":
            from sylphy_entity.cognition.llamacpp import LlamaCppCognition

            cognition_provider_instance = LlamaCppCognition(
                model=effective_cognition_model,
                base_url=cognition_base_url or shared_llamacpp_url,
                slot=llamacpp_slot,
            )
        else:
            # Legacy-only selection. Not used by the intended runtime.
            from sylphy_entity.cognition.ollama import OllamaCognition

            cognition_provider_instance = OllamaCognition(
                model=effective_cognition_model,
                base_url=cognition_base_url or DEFAULT_LEGACY_OLLAMA_URL,
            )
        cognition_model_name = effective_cognition_model

    config = load_entity_config()
    supervisor = EntitySupervisor(
        lock_path=runtime_dir / "entity-daemon.lock",
        port=port,
        sandbox_root=Path(sandbox_root) if sandbox_root else None,
        reconcile_interval_seconds=reconcile_interval,
        autonomy_enabled=autonomy,
        cognition_provider=cognition_provider_instance,
        cognition_model_name=cognition_model_name,
        conversation_generator=conversation_generator,
        identity_file=config.identity_file,
    )
    if voice_model and voice_voices:
        from sylphy_entity.voice.providers import KokoroOnnxProvider, SoundDeviceOutput

        supervisor.configure_voice_output(
            tts=KokoroOnnxProvider(voice_model, voice_voices),
            output=SoundDeviceOutput(),
        )
    try:
        await supervisor.start()
        await supervisor.serve_forever()
    finally:
        await supervisor.shutdown()
        # ConversationRuntime.stop() already closes the generator on the real
        # supervisor. Cognition close() is idempotent and covers stand-ins.
        if cognition_provider_instance is not None:
            await cognition_provider_instance.close()


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
        default=None,
        help="Optional legacy override; when unset Cognition inherits the authoritative --conversation-model",
    )
    parser.add_argument(
        "--cognition-provider",
        choices=["llama.cpp", "ollama"],
        default="llama.cpp",
        help="Cognition provider (default: llama.cpp; ollama is legacy-only)",
    )
    parser.add_argument(
        "--cognition-base-url", default=None,
        help="Optional cognition endpoint override (default: the Conversation llama.cpp URL)",
    )
    parser.add_argument(
        "--conversation-model", default=DEFAULT_LLAMACPP_MODEL,
        help="Authoritative selected model for Conversation and Cognition",
    )
    parser.add_argument(
        "--conversation-provider",
        choices=["ollama", "llama.cpp"],
        default="llama.cpp",
        help="Conversation model provider (default: llama.cpp)",
    )
    parser.add_argument(
        "--conversation-base-url",
        default=None,
        help="Base URL for the conversation provider (default: llama.cpp http://127.0.0.1:9931)",
    )
    parser.add_argument(
        "--conversation-thinking",
        choices=["on", "off"],
        default="on",
        help="Enable Gemma thinking for llama.cpp Conversation (default: on). Cognition always disables it.",
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
            port=args.port,
            sandbox_root=args.sandbox_root,
            reconcile_interval=args.reconcile_interval,
            autonomy=args.autonomy,
            conversation_model=args.conversation_model,
            conversation_provider=args.conversation_provider,
            conversation_base_url=args.conversation_base_url,
            conversation_thinking=args.conversation_thinking == "on",
            cognition_provider=args.cognition_provider,
            cognition_model=args.cognition_model,
            cognition_base_url=args.cognition_base_url,
            voice_model=args.voice_model,
            voice_voices=args.voice_voices,
        ))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Shutdown requested")
    except InstanceAlreadyRunning as exc:
        print(f"SYLPHY Entity is already running: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
