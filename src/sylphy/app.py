"""Interactive console entry point and application lifecycle."""

import logging
from threading import Event

from .config import load_config
from .avatar import AvatarController
from .camera import CameraService
from .cognition import CognitionService
from .context import ContextBuilder
from .core import ConversationCancelled, ConversationService
from .exploration import ExplorationService
from .identity import load_identity
from .investigation import InvestigationBudget
from .logging_setup import configure_logging
from .memory import MemoryService
from .runtime import Runtime
from .ollama_provider import OllamaProvider
from .storage import SQLiteStore
from .web_access import BraveSearchProvider, WebReader, WebAccessError
from .vision import MAX_QUESTION_CHARS, OllamaVisionProvider, VisionError
from .voice import VoiceError, VoiceService

logger = logging.getLogger(__name__)


def _memory_command(text: str, memories: MemoryService | None, session_id: str, store: SQLiteStore,
                    runtime: Runtime | None = None) -> None:
    parts = text.split(maxsplit=2)
    command = parts[1].casefold() if len(parts) > 1 else "help"
    argument = parts[2] if len(parts) > 2 else ""
    if command == "help":
        print("Memory: /memory list | search <query> | add <content> | update <id> <content> | history <id> | delete <id>")
        return
    if memories is None:
        print("Memory is disabled in config.toml.")
        return
    if command == "list":
        items = memories.list()
        if runtime:
            runtime.memory_operation("listed")
        for item in items:
            print(f"{item.id} | {item.category} | {item.content}")
        if not items:
            print("No confirmed memories.")
    elif command == "search":
        items = memories.search(argument)
        if runtime:
            runtime.memory_operation("searched")
        for item in items:
            print(f"{item.id} | {item.category} | {item.content}")
        if not items:
            print("No matching memories.")
    elif command == "add":
        messages = store.messages(session_id)
        source = next((message.id for message in reversed(messages) if message.role == "user"), None)
        try:
            item = memories.create(argument, source_session_id=session_id, source_message_id=source,
                                   origin="manual")
            if runtime:
                runtime.memory_operation("created", item.id)
            print(f"Memory saved: {item.id}")
        except ValueError as exc:
            print(exc)
    elif command == "update":
        update_parts = argument.split(maxsplit=1)
        if len(update_parts) != 2:
            print("Usage: /memory update <id> <content>")
            return
        try:
            item = memories.update(update_parts[0], update_parts[1], correction_origin="manual",
                                   correction_source_session_id=session_id)
            if runtime:
                runtime.memory_operation("updated", item.id)
            print(f"Memory updated: {item.id}")
        except (KeyError, ValueError) as exc:
            print(exc)
    elif command == "history":
        try:
            revisions = memories.history(argument)
            for revision in revisions:
                print(f"{revision.revision_at.isoformat()} | {revision.category} | "
                      f"{revision.content} | correction={revision.correction_origin}")
            if not revisions:
                print("No correction history.")
        except KeyError as exc:
            print(exc)
    elif command == "delete":
        try:
            item = memories.get(argument)
            if input(f"Type DELETE {item.id} to delete this memory: ") == f"DELETE {item.id}":
                memories.delete(item.id)
                if runtime:
                    runtime.memory_operation("deleted", item.id)
                print("Memory deleted.")
            else:
                print("Deletion cancelled.")
        except KeyError as exc:
            print(exc)
    else:
        print("Unknown memory command. Use /memory help.")


def _choose_session(store: SQLiteStore) -> str | None:
    while True:
        command = input("Session: [n]ew, [l]ist/resume, [q]uit: ").strip().casefold()
        if command in {"q", "quit", "/quit"}:
            return None
        if command in {"n", "new"}:
            title = input("Optional title: ").strip() or None
            session = store.create_session(title)
            print(f"Created session {session.id}" + (f" — {session.title}" if session.title else ""))
            return session.id
        if command in {"l", "list"}:
            sessions = store.list_sessions()
            for session in sessions:
                print(f"{session.id} | {session.title or '(untitled)'} | updated {session.updated_at.isoformat()}")
            if sessions:
                session_id = input("Session ID to resume (blank to return): ").strip()
                if store.get_session(session_id):
                    return session_id
                if session_id:
                    print("Session not found.")
            else:
                print("No saved sessions.")
            continue
        print("Choose n, l, or q.")


def _stream_reply(runtime: Runtime, service: ConversationService, session_id: str, text: str,
                  cognition: CognitionService | None, store: SQLiteStore) -> None:
    cancel = Event()
    print("SYLPHY: ", end="", flush=True)
    try:
        runtime.run_conversation(service, session_id, text, cancel=cancel,
                                 on_chunk=lambda chunk: print(chunk, end="", flush=True),
                                 cognition=cognition, persistence=store)
        print()
    except KeyboardInterrupt:
        cancel.set()
        print("\n[cancelling response; waiting for Ollama]")
        raise
    except ConversationCancelled:
        print("\n[cancelled; no response was saved]")
    except Exception as exc:
        logger.error("Conversation turn failed (%s)", type(exc).__name__)
        print(f"\nSYLPHY: {exc}")


def _vision_command(text: str, runtime: Runtime, service: ConversationService, session_id: str,
                    camera: CameraService, vision_provider: OllamaVisionProvider | None,
                    raw_responses: list[str] | None = None) -> None:
    parts = text.split(maxsplit=2)
    command = parts[1].casefold() if len(parts) > 1 else "status"
    argument = parts[2].strip() if len(parts) > 2 else ""
    if command == "status":
        config = camera.config
        provider_status = vision_provider.last_status if vision_provider else "not checked"
        print(f"Vision: {'enabled' if config.enabled else 'disabled'} | model: {config.model or '(not configured)'} | model status: {provider_status}")
        print(f"Camera index: {config.camera_index} | no camera is opened by status")
        return
    if command not in {"devices", "look"}:
        print("Usage: /vision status | /vision devices | /vision look <question>")
        return
    if command == "look" and not argument:
        print("Usage: /vision look <question>")
        return
    if command == "look" and len(argument) > MAX_QUESTION_CHARS:
        print(f"Visual question must be at most {MAX_QUESTION_CHARS} characters; no camera was opened.")
        return
    if not camera.config.enabled:
        print("Vision is disabled. Set [vision] enabled = true in config.toml to opt in.")
        return
    if command == "look" and vision_provider is None:
        print('No Vision model is configured. Set [vision].model = "lfm3-vl:2b-q8_0"; install manually with: ollama pull lfm3-vl:2b-q8_0')
        return

    def indicator(active: bool) -> None:
        if active:
            print("CAMERA ACTIVE — " + ("CAPTURING ONE FRAME" if command == "look" else "CHECKING DEVICES"), flush=True)
        else:
            print("CAMERA INACTIVE — capture/probe complete", flush=True)

    if command == "devices":
        try:
            devices = runtime.run_vision_devices(camera, indicator)
        except VisionError as exc:
            print(f"Vision camera error: {exc}")
            return
        if devices:
            print("Available camera indices: " + ", ".join(str(index) for index in devices))
        else:
            print("No available cameras found in the bounded index range 0–4.")
        return

    assert vision_provider is not None
    cancel = Event()
    print("SYLPHY: ", end="", flush=True)

    def show_raw_response() -> None:
        if raw_responses:
            print("\n--- Vision model raw response (terminal-only diagnostic) ---")
            print(raw_responses.pop(0))
            print("--- end raw Vision response ---")

    try:
        runtime.run_vision_conversation(
            service, session_id, argument, camera, vision_provider, cancel=cancel,
            on_chunk=lambda chunk: print(chunk, end="", flush=True),
            on_capture_state=indicator,
        )
        show_raw_response()
        print()
    except KeyboardInterrupt:
        cancel.set()
        print("\n[cancelling Vision conversation; waiting for local operation]")
        raise
    except (VisionError, InterruptedError, ValueError, RuntimeError) as exc:
        show_raw_response()
        logger.info("Vision request ended (%s)", type(exc).__name__)
        print(f"\nSYLPHY: Vision request failed: {exc}")


def _deliver_presence(runtime: Runtime) -> None:
    """Deliver pending events only from the foreground CLI prompt boundary."""
    if not runtime.presence.enabled or not runtime.presence.started:
        return
    runtime.restore_pending_presence()
    for event in runtime.presence.pending():
        if runtime.deliver_presence(event):
            print(f"\n[SYLPHY PRESENCE — {event.title}] {event.content}")


def _prompt_user(runtime: Runtime) -> str:
    """Deliver queued Presence before entering blocking terminal input."""
    _deliver_presence(runtime)
    return input("You: ").strip()


def _presence_command(text: str, runtime: Runtime) -> None:
    parts = text.split(maxsplit=2)
    command = parts[1].casefold() if len(parts) > 1 else "status"
    argument = parts[2] if len(parts) > 2 else ""
    presence = runtime.presence
    if command == "status":
        print(f"Presence: {'enabled' if presence.enabled else 'disabled'} | pending: {len(presence.pending())}/{presence.capacity}")
    elif command == "enable":
        runtime.set_presence_enabled(True)
        print("Presence enabled for this runtime.")
    elif command == "disable":
        runtime.set_presence_enabled(False)
        print("Presence disabled; pending items are retained but not delivered.")
    elif command == "list":
        events = presence.pending()
        for event in events:
            print(f"{event.event_id} | {event.event_type.value} | {event.title} | {event.content}")
        if not events:
            print("No pending Presence items.")
    elif command == "dismiss" and argument:
        print("Presence item dismissed." if runtime.dismiss_presence(argument) else "Presence item not found.")
    else:
        print("Usage: /presence status|enable|disable|list|dismiss <event-id>")


def _avatar_command(text: str, runtime: Runtime) -> None:
    avatar = runtime.avatar
    if avatar is None:
        print("Avatar is not configured.")
        return
    parts = text.split(maxsplit=1)
    command = parts[1].casefold() if len(parts) > 1 else "status"
    if command == "show":
        print("Avatar shown." if avatar.show() else "Avatar is unavailable or disabled.")
    elif command == "hide":
        print("Avatar hidden." if avatar.hide() else "Avatar is unavailable or disabled.")
    elif command in {"topmost on", "topmost off"}:
        enabled = command.endswith("on")
        print("Avatar always-on-top updated." if avatar.set_always_on_top(enabled)
              else "Avatar is unavailable or disabled.")
    elif command == "status":
        status = avatar.status()
        print(f"Avatar: {'enabled' if status.enabled else 'disabled'} | "
              f"{'available' if status.available else 'unavailable'} | "
              f"{'visible' if status.visible else 'hidden'} | state={status.state.value} | "
              f"always-on-top={'on' if status.always_on_top else 'off'}")
        if status.error:
            print(f"Avatar detail: {status.error}")
    else:
        print("Usage: /avatar status|show|hide|topmost on|topmost off")


def _voice_command(text: str, voice: VoiceService) -> None:
    parts = text.split(maxsplit=1)
    command = parts[1].casefold() if len(parts) > 1 else "status"
    if command == "status":
        state = voice.state
        print(f"Voice: {'enabled' if voice.config.enabled else 'disabled'} | state: {state}" +
              (f" | error: {voice.error}" if voice.error else ""))
    elif command == "devices":
        try:
            devices = voice.devices()
            for device in devices:
                print(f"{device.index} | {device.name} | inputs={device.input_channels} | {device.default_sample_rate:g} Hz")
            if not devices:
                print("No microphone devices found.")
        except VoiceError as exc:
            print(f"Voice device error: {exc}")
    elif command == "start":
        try:
            voice.start()
            print("Voice worker starting. Microphone access begins only after this command.")
        except VoiceError as exc:
            print(exc)
    elif command == "stop":
        print("Voice stopped." if voice.stop() else "Voice is still stopping; check /voice status.")
    elif command == "test":
        try:
            devices = voice.devices()
            if not devices:
                print("No microphone devices found.")
            else:
                print(f"Audio input is available ({len(devices)} microphone device(s)); /voice start begins listening.")
            if not voice.config.enabled:
                print("Voice is disabled; enable [voice].enabled in config.toml to use it.")
            else:
                missing = [str(path) for path in (
                    voice.config.model_dir / voice.config.tts_model,
                    voice.config.model_dir / voice.config.tts_voices,
                ) if not path.is_file()]
                if missing:
                    print("Kokoro assets missing: " + ", ".join(missing))
                else:
                    print(f"Kokoro voice assets found; Whisper {voice.config.whisper_model} loads on voice start.")
        except VoiceError as exc:
            print(f"Voice test failed: {exc}")
    else:
        print("Usage: /voice status|devices|start|stop|test")


def _exploration_command(text: str, runtime: Runtime, store: SQLiteStore) -> None:
    parts = text.split(maxsplit=2)
    command = parts[1].casefold() if len(parts) > 1 else "status"
    argument = parts[2] if len(parts) > 2 else ""
    worker = runtime.exploration_worker
    if command == "status":
        counts = store.exploration_counts()
        print(f"Exploration: {'enabled' if worker else 'disabled'} | worker: {'running' if worker and worker.running else 'stopped'}")
        print(f"Tasks: pending={counts.get('task_pending', 0)} running={counts.get('task_running', 0)} failed={counts.get('task_failed', 0)} | questions pending={counts.get('question_pending', 0)}")
        if worker and worker.last_error:
            print(f"Last worker error: {worker.last_error}")
    elif command == "list":
        questions = store.list_exploration_questions()
        for question in questions:
            print(f"{question.id} | {question.question} | {question.topic or 'untagged'} | expires {question.expires_at.isoformat()}")
        if not questions:
            print("No pending exploration questions.")
    elif command == "dismiss" and argument:
        try:
            runtime.dismiss_exploration_question(argument, store)
            print("Exploration question dismissed.")
        except (KeyError, ValueError) as exc:
            print(exc)
    else:
        print("Usage: /exploration status|list|dismiss <id>")


def _investigation_command(text: str, runtime: Runtime) -> None:
    parts = text.split(maxsplit=2)
    command = parts[1].casefold() if len(parts) > 1 else "help"
    argument = parts[2] if len(parts) > 2 else ""
    if command == "help":
        print("Investigation: /investigation list | inspect <id> | start <exploration-question-id> | cancel <id>")
        return
    if not runtime.investigations_enabled:
        print("Investigation management is disabled in config.toml.")
        return
    try:
        if command == "list":
            items = runtime.list_investigations()
            for item in items:
                print(f"{item.id} | {item.status.value} | {item.question} | attempts={item.attempt_count}/{item.max_attempts}")
            if not items:
                print("No investigations.")
        elif command == "inspect" and argument:
            item = runtime.get_investigation(argument)
            if item is None:
                print(f"Investigation not found: {argument}")
            else:
                print(f"ID: {item.id}\nQuestion: {item.question}\nStatus: {item.status.value}")
                print(f"Created: {item.created_at.isoformat()}\nUpdated: {item.updated_at.isoformat()}\nExpires: {item.expires_at.isoformat()}")
                print(f"Attempts: {item.attempt_count}/{item.max_attempts}\nDeferred: {item.deferred_at.isoformat() if item.deferred_at else '-'}")
                print(f"Budget used: searches={item.search_calls_used}/{item.budget.max_search_requests}, pages={item.pages_read}/{item.budget.max_pages}, model={item.model_calls_used}/{item.budget.max_model_calls}, bytes={item.source_bytes_used}/{item.budget.max_source_bytes}, text={item.extracted_chars_used}/{item.budget.max_extracted_text_chars}")
                print(f"Error: {item.last_error or '-'}")
                for evidence in runtime.get_investigation_evidence(item.id):
                    preview = " ".join(evidence.text.split())[:180]
                    print(f"Source [{evidence.id}] {evidence.status}: {evidence.title[:160]} | {evidence.final_url or evidence.source_url}")
                    if preview:
                        print(f"  {preview}")
                    if evidence.error:
                        print(f"  Limitation: {evidence.error[:240]}")
                result = runtime.get_investigation_result(item.id)
                if result:
                    print(f"Summary: {result.summary[:1000]}\nEvidence sufficient: {result.evidence_sufficient}\nUncertainty: {result.uncertainty[:800]}")
                    for finding in result.findings:
                        print(f"Finding: {finding.text[:500]} | evidence: {', '.join(finding.evidence_ids)}")
                    for limitation in result.limitations:
                        print(f"Limitation: {limitation[:300]}")
        elif command == "start" and argument:
            if not runtime.investigation_execution_enabled:
                print("Investigation execution requires Investigation, Web Search, and Web Reader enabled.")
                return
            existing = runtime.get_investigation_for_question(argument)
            if existing is not None and existing.status.value != "queued":
                item = runtime.start_investigation(argument, allow_external_search=True)
                print(f"Investigation {item.id} is {item.status.value}.")
                return
            question = runtime.get_exploration_question(argument)
            if question is None:
                print(f"Exploration question not found: {argument}")
                return
            print("This question will be sent to the configured Search provider:")
            print(question.question)
            if input("Proceed with this external Search request? [y/N] ").strip().casefold() != "y":
                print("Investigation was not started.")
                return
            item = runtime.start_investigation(argument, allow_external_search=True)
            print(f"Investigation {item.id} is {item.status.value}.")
        elif command == "cancel" and argument:
            item = runtime.cancel_investigation(argument)
            print(f"Investigation {item.id} is {item.status.value}.")
        else:
            print("Usage: /investigation list|inspect <id>|start <question-id>|cancel <id>")
    except (KeyError, ValueError, RuntimeError) as exc:
        print(exc)


def _web_command(text: str, search_provider: BraveSearchProvider | None,
                 web_reader: WebReader | None) -> None:
    parts = text.split(maxsplit=2)
    operation = parts[1].casefold() if len(parts) > 1 else "help"
    argument = parts[2] if len(parts) > 2 else ""
    if operation == "help":
        print("Web: /web search <query> | /web read <http(s)-url>")
        return
    try:
        if operation == "search" and argument:
            if search_provider is None:
                print("Web Search is disabled in config.toml.")
                return
            for item in search_provider.search(argument):
                print(f"{item.title} | {item.url}\n{item.snippet}\n")
            return
        if operation == "read" and argument:
            if web_reader is None:
                print("Web Reader is disabled in config.toml.")
                return
            page = web_reader.read(argument)
            print(f"{page.title or '(untitled)'} | {page.final_url} | HTTP {page.status}")
            print(page.text)
            return
    except WebAccessError as exc:
        print(f"Web request failed: {exc}")
        return
    print("Usage: /web search <query>|/web read <http(s)-url>")


def main() -> None:
    config = load_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(config.log_dir)
    identity = load_identity(config.identity_file)
    store = SQLiteStore(config.database_path)
    memory_service = MemoryService(store) if config.memory_enabled else None
    runtime = Runtime(config.ollama_model, config.max_concurrent_generations,
                      config.heartbeat_enabled, config.heartbeat_interval_seconds,
                      config.presence_enabled, config.presence_queue_capacity)
    avatar = AvatarController(config.avatar)
    runtime.configure_avatar(avatar)
    if config.avatar.config_error:
        logger.warning("Avatar configuration issue: %s", config.avatar.config_error)
    provider = OllamaProvider(config.ollama_host, config.ollama_model, config.ollama_timeout_seconds)
    camera = CameraService(config.vision)
    raw_vision_responses: list[str] = []
    vision_provider = (OllamaVisionProvider(config.ollama_host, config.vision.model,
                                            config.vision.inference_timeout_seconds,
                                            on_raw_response=raw_vision_responses.append)
                       if config.vision.enabled and config.vision.model else None)
    service = ConversationService(
        provider,
        store,
        ContextBuilder(config.system_instructions, identity.to_prompt(), config.context_budget,
                       memory_retriever=memory_service,
                       memory_budget=config.memory_context_budget if memory_service else 0,
                       memory_limit=config.memory_max_items if memory_service else 0),
    )
    voice = VoiceService(
        config.voice, runtime, service, lambda: session_id,
        on_transcript=lambda transcript: print(f"\nYou (voice): {transcript}", flush=True),
        on_response=lambda response: print(f"SYLPHY: {response}", flush=True),
    )
    runtime.configure_voice(voice)
    cognition = CognitionService(provider, store, memory_service, config.cognition_enabled,
                                 config.cognition_max_candidates, config.cognition_max_context_messages,
                                 max_confirmed_memory_items=config.memory_max_items,
                                 memory_context_budget=config.memory_context_budget) if memory_service else None
    if cognition and cognition.enabled:
        runtime.configure_cognition_worker(store, cognition, config.cognition_max_pending_tasks,
                                           config.cognition_shutdown_grace_seconds)
    if config.exploration_enabled:
        exploration = ExplorationService(
            provider, store, memory_service,
            context_max_chars=config.exploration_context_max_chars,
            question_max_length=config.exploration_question_max_length,
            question_expiry_days=config.exploration_question_expiry_days,
            memory_limit=config.memory_max_items if memory_service else 0,
            max_queue_items=config.exploration_max_queue_items,
        )
        runtime.configure_exploration_worker(
            store, exploration, config.exploration_max_queue_items,
            config.exploration_cooldown_seconds, config.exploration_max_attempts_per_period,
            config.exploration_period_seconds, config.exploration_question_expiry_days,
        )
    if config.investigation_enabled:
        runtime.configure_investigations(
            store,
            InvestigationBudget(
                config.investigation_max_duration_seconds,
                config.investigation_max_model_calls,
                config.investigation_max_search_requests,
                config.investigation_max_pages,
                config.investigation_max_source_bytes,
                config.investigation_max_extracted_text_chars,
            ),
            config.investigation_max_attempts,
        )
    search_provider = None
    if config.web_search_enabled:
        search_provider = BraveSearchProvider(
            timeout_seconds=config.web_search_timeout_seconds,
            max_results=config.web_search_max_results,
            max_response_bytes=config.web_search_max_response_bytes,
        )
    web_reader = None
    if config.web_reader_enabled:
        web_reader = WebReader(
            connect_timeout_seconds=config.web_reader_connect_timeout_seconds,
            read_timeout_seconds=config.web_reader_read_timeout_seconds,
            total_timeout_seconds=config.web_reader_total_timeout_seconds,
            max_redirects=config.web_reader_max_redirects,
            max_response_bytes=config.web_reader_max_response_bytes,
            max_extracted_chars=config.web_reader_max_extracted_chars,
            max_concurrent_requests=config.web_reader_max_concurrent_requests,
        )
    if config.investigation_enabled and search_provider is not None and web_reader is not None:
        runtime.configure_investigation_execution(
            store,
            InvestigationBudget(
                config.investigation_max_duration_seconds,
                config.investigation_max_model_calls,
                config.investigation_max_search_requests,
                config.investigation_max_pages,
                config.investigation_max_source_bytes,
                config.investigation_max_extracted_text_chars,
            ),
            config.investigation_max_attempts,
            search_provider,
            web_reader,
            provider,
        )
    print("SYLPHY — local conversation. Sessions are stored locally. Use /quit to return to session selection.")
    try:
        runtime.start()
        while session_id := _choose_session(store):
            runtime.change_session(session_id)
            session = store.get_session(session_id)
            print(f"\nSession: {session.title or session.id}")
            for message in store.messages(session_id):
                print(f"{'You' if message.role == 'user' else 'SYLPHY'}: {message.content}")
            while True:
                text = _prompt_user(runtime)
                if text.casefold() in {"/quit", "/sessions"}:
                    runtime.change_session(None)
                    break
                if text.casefold() == "/rename":
                    title = input("New title (blank clears): ")
                    store.rename_session(session_id, title)
                    print("Session renamed.")
                    continue
                if text.casefold() == "/delete":
                    confirm = input(f"Type DELETE {session_id} to permanently delete this session: ")
                    if confirm == f"DELETE {session_id}":
                        store.delete_session(session_id)
                        runtime.change_session(None)
                        print("Session deleted.")
                        break
                    print("Deletion cancelled.")
                    continue
                if text.casefold().startswith("/memory"):
                    _memory_command(text, memory_service, session_id, store, runtime)
                    continue
                if text.casefold().startswith("/presence"):
                    _presence_command(text, runtime)
                    continue
                if text.casefold().startswith("/avatar"):
                    _avatar_command(text, runtime)
                    continue
                if text.casefold().startswith("/voice"):
                    _voice_command(text, voice)
                    continue
                if text.casefold().startswith("/vision"):
                    _vision_command(text, runtime, service, session_id, camera, vision_provider,
                                    raw_vision_responses)
                    continue
                if text.casefold().startswith("/exploration"):
                    _exploration_command(text, runtime, store)
                    continue
                if text.casefold().startswith("/investigation"):
                    _investigation_command(text, runtime)
                    continue
                if text.casefold().startswith("/web"):
                    _web_command(text, search_provider, web_reader)
                    continue
                if text.casefold().startswith("/cognition"):
                    parts = text.split(maxsplit=2)
                    command = parts[1].casefold() if len(parts) > 1 else "status"
                    if command == "status":
                        counts = store.cognition_task_counts()
                        worker = runtime.cognition_worker
                        print(f"Cognition: {'enabled' if cognition and cognition.enabled else 'disabled'} | worker: {'running' if worker and worker.running else 'stopped'}")
                        print(f"Current task: {worker.current_task if worker and worker.current_task else '-'} | pending: {counts.get('pending', 0)} | completed: {counts.get('completed', 0)} | failed: {counts.get('failed', 0)}")
                        if worker and worker.last_error:
                            print(f"Last worker error: {worker.last_error}")
                    elif cognition is None:
                        print("Cognition is unavailable while memory is disabled.")
                    elif command == "tasks":
                        for task in store.list_cognition_tasks():
                            print(f"{task.id} | {task.status} | {task.source_session_id} | {task.created_at.isoformat()} | attempts={task.attempt_count}")
                    elif command == "list":
                        for candidate in cognition.list_pending():
                            print(f"{candidate.id} | {candidate.category} | {candidate.content}")
                    elif command == "confirm" and len(parts) == 3:
                        try:
                            memory = cognition.confirm(parts[2])
                            print(f"Candidate confirmed as memory {memory.id}")
                        except (KeyError, ValueError) as exc:
                            print(exc)
                    elif command == "reject" and len(parts) == 3:
                        try:
                            cognition.reject(parts[2])
                            print("Candidate rejected.")
                        except (KeyError, ValueError) as exc:
                            print(exc)
                    else:
                        print("Usage: /cognition status|tasks|list|confirm <id>|reject <id>")
                    continue
                if text.casefold() in {"/status", "/runtime"}:
                    snapshot = runtime.state.snapshot()
                    print(f"Runtime: {snapshot.phase.value} | session: {snapshot.session_id or '-'} | model: {snapshot.model}")
                    print(f"Generation: {'active' if snapshot.generation_active else 'idle'} | uptime: {snapshot.uptime_seconds:.1f}s | last activity: {snapshot.last_activity.isoformat()}")
                    if snapshot.last_error:
                        print(f"Last error: {snapshot.last_error}")
                    continue
                if text.casefold() == "/help":
                    print("Commands: /quit, /rename, /delete, /status, /memory help, /presence status|enable|disable|list|dismiss, /avatar status|show|hide|topmost on|topmost off, /voice status|devices|start|stop|test, /vision status|devices|look <question>, /exploration status|list|dismiss, /investigation help, /web help")
                    continue
                if text and text not in {"/exit"}:
                    _stream_reply(runtime, service, session_id, text, cognition, store)
    except (KeyboardInterrupt, EOFError):
        print("\nSYLPHY: Goodbye.")
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    main()
