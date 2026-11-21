# SYLPHY G4 Presence Core

Local-first Windows console companion with persistent sessions and text conversations through a local Ollama server. No cloud inference or telemetry is configured.

## Requirements

- Windows and Python 3.10 or newer (uses the standard-library TOML reader).
- Ollama installed and running locally with at least one model pulled.

Python 3.10.11, Ollama 0.40.16, and the installed model list were inspected for this workspace. The live integration check uses `spark-t1000:latest` by default.

## Setup and run

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m sylphy.app
```

Or install the `sylphy` command with the package and run `sylphy`. Edit `config.toml` to select a model and set the context and memory budgets. `OLLAMA_HOST`, `OLLAMA_MODEL`, and `SYLPHY_CONFIG` can override endpoint/model/config path; `SYLPHY_CONTEXT_BUDGET` and `SYLPHY_IDENTITY_FILE` override context and identity settings. Sessions, messages, and memories are stored under `data\sylphy.sqlite3`; rotating logs go under `logs\`. G0 `turns` databases are migrated into sessions/messages on open; G3 adds the memories table without removing existing data.

At startup choose `n` to create a session, or `l` to list and resume a saved one. In a session, `/quit` returns to session selection, `/rename` changes its title, and `/delete` prompts for the exact text `DELETE <session-id>` before removing it and its messages. Ctrl+C during generation cancels the stream; the incomplete assistant output is not saved.

System instructions and the context token budget are configured in `config.toml`. Identity v1 is loaded from `identity.toml`, independently of Python source and the Ollama provider. Its core character and stable privacy boundaries are configured there; the system instructions complement Identity, while deterministic interaction rules remain application code. Missing or invalid identity config fails startup rather than silently substituting another identity.

Context assembly is centralized in `sylphy.context.ContextBuilder`. It sends system instructions, identity, selected history, then the current message. The default budget is 8192 estimated tokens. G2 uses a lightweight approximation of one token per four UTF-8 bytes; it is deterministic but not model-tokenizer exact. The builder keeps the newest whole history messages that fit and restores chronological order. System instructions, identity, and the current user message are never dropped; if those alone exceed the budget, the request fails with a clear overflow error.

## Persistent memory

G3 stores confirmed memories in the same SQLite database as sessions and messages. Each record has a stable ID, content, category, created/updated timestamps, optional source session/message IDs, optional confidence, and status. Memory search is local lexical matching: query terms longer than two characters are counted in confirmed memory text and ranked by matches. No embeddings or automatic memory writes are used; cognition proposals require manual confirmation.

Inside a session, use `/memory help` for commands:

- `/memory list`
- `/memory search <query>`
- `/memory add <content>` (saved as category `other`, with the current session and latest user message as provenance when available)
- `/memory update <id> <content>`
- `/memory delete <id>` (requires typing `DELETE <id>`)

The context builder retrieves at most `memory.max_items` matching confirmed memories, bounded by `memory.context_budget` and the overall `ollama.context_budget`; memories compete with history for the overall budget. If no memories match, context is assembled as before. Set `[memory] enabled = false` in `config.toml` to disable both retrieval and CLI management without deleting stored memories.

## Cognition candidates (G5.0–G5.2)

Post-turn cognition is disabled by default. When `[cognition] enabled = true`, the completed turn is persisted by the conversation service before a task is added to the SQLite queue. The user-facing response does not wait for analysis; enqueue errors are logged and do not undo that response. One Runtime-owned worker processes tasks sequentially with the existing local Ollama provider. Responses must be a JSON object containing a `candidates` array; every candidate must have non-empty content, a supported category, and finite confidence from 0 to 1. Invalid output fails the task and uses the existing bounded retry instead of being counted as an empty analysis. A task with an explicit empty candidates array is successful. Inference and persistence errors are recorded and retried once, then marked failed. A restart returns interrupted RUNNING tasks to the queue. Duplicate source turns map to the same task. Review proposals with `/cognition list`, then `/cognition confirm <id>` or `/cognition reject <id>`. `/cognition status` reports worker and task counts; `/cognition tasks` lists recent task states.

Cognition can include lexically relevant confirmed memories in its analysis prompt, bounded by the existing `[memory] max_items` and `[memory] context_budget` settings. Memory text is labeled as untrusted user-provided data. Exact normalized duplicates of retrieved confirmed memory content are filtered before candidate persistence. Candidate deduplication across turns is conservative: only case/whitespace-normalized exact content with the same category is merged, inside a serialized SQLite write transaction. The first candidate ID, status, confidence, timestamp, and source provenance remain canonical; paraphrases are not merged, and duplicate occurrences are not separately recorded. No memory is created or changed without manual confirmation.

The single inference slot gives conversations priority. If a conversation arrives while cognition is streaming, the shared provider cancellation event asks cognition to close its stream at the next received chunk; the interrupted task returns to PENDING without consuming a retry. If a stream stops producing chunks, the Ollama client's configured request timeout (120 seconds by default) bounds the wait. No parallel model inference is started. On shutdown, the heartbeat stops and the worker gets `[cognition] shutdown_grace_seconds`; if it is still in a non-interruptible model call, shutdown reports a timeout and the runtime remains ERROR instead of claiming STOPPED. The RUNNING task remains recoverable on the next startup. Cognition is best-effort and does not alter completed conversation persistence semantics. Analysis prompts treat conversation text as data, but prompt instructions are not a security boundary.

## Tests

```powershell
python -m pytest -q
python -m pytest -q -m integration
```

The integration test needs a running local Ollama server and configured test model. Set `SYLPHY_TEST_MODEL` to test another installed model. It writes only to a temporary integration database (override with `SYLPHY_TEST_DB`).

## Local model benchmark

Benchmarking is opt-in and separate from normal application startup:

```powershell
python -m sylphy.benchmark --models spark-t1000:latest --contexts 256 2048 8192
```

The command runs each requested context once, only for models already installed in Ollama, and prints JSON containing requested context size, Ollama prompt token count, time to first output token, generation tokens/second, total time, generated token count, and errors. The size controls approximate prompt payload (four UTF-8 bytes per requested token); actual prompt tokens are reported by Ollama. First output token includes a model's thinking stream when present. Results vary with model loading, GPU/CPU placement, thermal state, and other host activity; benchmark before comparing runs.

## Runtime presence

G4 wraps the existing conversation and memory services with an in-process `Runtime`. It tracks lifecycle phase, active session/model/operation, generation activity, timestamps, uptime, last error, and background task count in read-only snapshots. This is operational state only; it is not persisted and is never added to conversation context.

`EventBus` dispatches local events synchronously. Subscribers can unsubscribe; handler exceptions are logged and isolated. A daemon heartbeat emits a `HEARTBEAT` event every configured interval and reports runtime status. It does not contact Ollama or invoke a model. Shutdown signals and joins it before clearing event subscriptions. Disable it with `[runtime] heartbeat_enabled = false`.

`ResourceGovernor` protects generation with one cancellable slot. Configuration rejects concurrency above one because the current runtime state and CLI support one active conversation generation. The CLI adds `/status` and `/runtime` for a concise state snapshot, plus `/help`.

## Presence boundary (G6.1)

Presence is disabled by default. `[presence] enabled = true` enables it for future internal publishers; `/presence enable` and `/presence disable` toggle it for the current runtime only. Missing `[presence]` fields remain disabled with a queue capacity of 20. `/presence status`, `/presence list`, and `/presence dismiss <event-id>` inspect or remove pending items.

The current event contract supports only `notice`. Events require a short title and content, have an ID and timestamp, and may include opaque source and deduplication identifiers. Titles are limited to 120 characters and content to 1000. Obvious URLs, filesystem paths, and terminal control characters are rejected. The in-memory FIFO holds up to the configured capacity; when full it rejects the newest event and retains older pending items. General Presence events are in-memory and deduplicate only while pending.

Attention/Delivery Gate v0 connects a successfully saved, eligible Exploration question to Presence when Presence is enabled. The question must still be pending, unexpired, and owned by its persisted source turn. Its notification lifecycle is stored separately from question status in SQLite (`queued`, `delivered`, `dismissed`, or `expired`), so a drained or dismissed notice is not republished after restart. Queued notices are restored into the bounded in-memory FIFO when Runtime starts or Presence is enabled. Notice dismissal suppresses future delivery but leaves the Exploration question pending; `/exploration dismiss <id>` dismisses the question and suppresses/removes its queued notice.

The CLI delivers and records queued notices only at the next prompt boundary, before blocking on `You:`. Nothing prints from a background thread. Claiming delivery in SQLite immediately before writing to stdout provides at-most-once behavior across restarts; a process crash after the claim but before output can lose that notice. Delivery does not invoke a model, change conversation history, create Memory, or perform actions. Presence owns no worker thread and is started/stopped with Runtime.

Presence itself does not monitor desktop, filesystem, network, or time events, or generate content. Its only current publisher is the existing Exploration question gate; deterministic eligibility checks are in the small Interaction Policy boundary. No environmental context is collected. General Presence events remain in-memory; only Exploration notification lifecycle is persisted with its source question.

## Exploration question seed (G6.2)

Exploration is disabled by default. Set `[exploration] enabled = true` in `config.toml` to let completed conversation turns become eligible for one local question-forming inference. This is a separate Runtime-owned worker; it is awakened by accepted completed-turn events and has no polling loop or scheduler. Deterministic gates apply a one-hour minimum cooldown, at most three inference attempts per 24 hours, and a combined pending-work/question queue capacity of 20 by default. Attempts and source-turn tasks are persisted in SQLite. Running tasks return to pending after restart.

The worker can see a bounded recent conversation and lexically relevant confirmed memories. Its prompt treats both as untrusted data and requests either an empty result or one grounded question with a quote copied from the supplied context. Output is strictly validated, including a maximum question length and quote-presence check. Questions are deduplicated by case/whitespace-normalized text, stored separately from Memory with source session/message and retrieved-memory provenance, and expire after 30 days by default. They never create or modify Confirmed Memory. The quote check confirms that the evidence text appears in the input; it does not prove that the proposed question is a sound inference.

The shared inference slot supports conversation, Cognition, and Exploration priorities. Conversation can cancel lower-priority streaming work; Cognition also has priority over Exploration. No parallel inference is started. Cancellation is observed by the Ollama adapter when stream chunks arrive, so a silent stream can delay preemption until the configured request timeout. Provider or validation failures are persisted as failed tasks and are not automatically retried; cancellation returns work to pending. Queue questions can be inspected or dismissed with `/exploration list` and `/exploration dismiss <id>`; `/exploration status` shows worker and queue state. Enablement is configuration-only and does not have a runtime toggle.

Exploration does not perform research, access the network or filesystem, run reflection loops, or automatically confirm Memory. It remains limited to question formation from already persisted local conversation and confirmed-memory text. Presence delivery is separate from Exploration question formation and does not start Investigation.

## Identity & Interaction Policy v1

Identity is a configurable definition in `identity.toml`. The deterministic policy contract in `src/sylphy/interaction_policy.py` defines `SPEAK`, `EXPLORE`, `WAIT`, `REQUEST_PERMISSION`, and `SILENCE` with stable reason codes and event provenance. Only the saved Exploration question to Presence path is currently connected; other outcomes are available to callers but are not wired to hypothetical Vision, Avatar, or autonomous discovery sources. `WAIT` requires the caller to indicate that deferral is supported; no scheduler is added. Exploration's cooldown limits its inference attempts; it is not a global Presence delivery cooldown.

## Desktop avatar (G7 Avatar Foundation v1)

The repository's operational interface is the existing synchronous terminal loop in `src/sylphy/app.py` (there is no Textual dependency or Textual UI in this checkout). The optional native Windows avatar window runs on its own owned message-loop thread and does not replace or share an event loop with the terminal interface. Avatar is disabled by default. Set `[avatar] enabled = true` to opt in; `/avatar status`, `/avatar show`, `/avatar hide`, and `/avatar topmost on|off` control it during a session. The window's title bar moves it; its close button hides it, and Runtime shutdown releases its UI thread and graphics resources.

The initial asset is `assets/avatar/sylphy.png`. Only PNG is supported. Paths must stay under `assets/avatar/` unless `allow_external_asset = true` explicitly approves another local path. Missing, invalid, or unapproved assets display an in-memory **SYLPHY DEVELOPMENT PLACEHOLDER** and an inspectable status message; no character artwork is included or downloaded. Width and height are bounded to 120–800 pixels. Position is session-local. Always-on-top is off by default and can be changed in the current runtime. No startup registration or network access is used.

The controller reports `IDLE`, `THINKING`, `SPEAKING`, and grounded `CURIOUS` updates from Runtime. `OBSERVING` is rejected until an authorized Vision subsystem exists. State updates never invoke inference or activate the window. The native window uses no-activate behavior so state changes do not take keyboard focus from the terminal. Closing/hiding the avatar does not stop conversation. Pillow is used only for bounded PNG loading and aspect-preserving composition; the window itself uses Windows APIs and Python `ctypes`.

Manual Windows acceptance: enable the avatar, start SYLPHY, verify the placeholder or approved PNG opens in a separate movable window, exercise `/avatar hide` and `/avatar show`, toggle always-on-top, close the window, confirm the terminal remains usable, and exit SYLPHY to verify the window thread ends. The automated suite uses fake surfaces and does not require an interactive desktop.

## Visual Perception Foundation v1 (one-shot, opt-in)

Vision is disabled by default. The installed local model selected for Vision is `lfm3-vl:2b-q8_0` (Ollama metadata advertises the `vision` capability); the existing conversation model is unchanged. Set `[vision] enabled = true` to opt in, then use `/vision status`, `/vision devices`, or `/vision look <question>`. `status` performs no camera or model request. `devices` explicitly probes indices 0–4 and releases each probe. `look` checks model capability before capture, opens only the configured index, captures one frame, releases it, then performs local image analysis and one normal SYLPHY conversation response.

Camera support is optional and is not needed for text-only startup. Install it manually from the project root with `python -m pip install -e ".[vision]"`. The backend uses OpenCV's Windows Media Foundation capture in an isolated child process so a blocked camera driver can be terminated at the configured timeout. Frames are resized and JPEG-encoded in memory, bounded by `max_width`, `max_height`, and `max_image_bytes`; they are never written to disk. Vision and conversation inference use the existing single ResourceGovernor slot sequentially. The short-lived observation is labeled as untrusted context for that turn only; the ordinary question and answer retain normal conversation persistence. The Vision route does not enqueue Cognition or Exploration work.

```toml
[vision]
enabled = false
model = "lfm3-vl:2b-q8_0"
camera_index = 0
capture_timeout_seconds = 5
max_width = 1280
max_height = 1280
jpeg_quality = 85
max_image_bytes = 1500000
inference_timeout_seconds = 90
```

The configured Vision endpoint must be unauthenticated loopback HTTP. If the selected model is missing, install it only by explicit manual action: `ollama pull lfm3-vl:2b-q8_0`. Vision describes image content; text visible in an image and model output are untrusted and cannot authorize tools or actions. No face recognition, continuous capture, image archive, automatic Memory, or cloud image transfer is implemented. OpenCV's driver behavior and camera privacy prompts still require manual verification on the target Windows desktop.

## Investigation execution (G6.3-B2)

Investigation remains disabled by default. To explicitly start a research pass, enable `[investigation]`, `[web_search]`, and `[web_reader]`; supply `BRAVE_SEARCH_API_KEY` through the environment. `/investigation start <exploration-question-id>` starts one synchronous, user initiated pass from an unexpired pending Exploration question. The stored question itself is sent to the configured Search provider (Brave Search); review it before starting if it contains sensitive details. It uses only that question as the Search query and gives the local model only that question plus bounded retrieved evidence. No conversation history, Memory, identity prompt, files, or credentials are sent to Search or analysis. A question has at most one durable Investigation; completed, failed, cancelled, or expired work is not silently restarted.

The pass performs at most one provider-configured Search request, selects up to three distinct valid HTTP(S) results, reads them through the existing secure Web Reader, then makes one structured local analysis call. Evidence (including URL, provider/rank, snippet, retrieval state, extracted text and hash) is stored separately from findings. Analysis must return JSON with a summary, findings citing only retrieved evidence IDs, uncertainty, limitations, and an evidence sufficiency flag. Invalid output or fabricated references fail the Investigation while collected evidence remains available. Search/page/content budgets are charged and persisted; per-request reader limits are reduced to fit the remaining total byte and extracted-text budgets. If the page/content ceiling prevents reading a source, that is reported as a limitation. No memory is automatically created or changed.

`/investigation list`, `/investigation inspect <id>`, and `/investigation cancel <id>` manage and inspect work. Inspection prints bounded source and finding previews, citations, errors, and budget use; it does not dump full page text. Because `start` is synchronous in this CLI, press Ctrl+C to cancel the active pass; `/investigation cancel` cancels queued work or work controlled through another Runtime caller. There is no research worker or automatic Internet access. Search and page operations do not hold the inference slot. During analysis the shared ResourceGovernor orders Conversation, Cognition, Exploration, then Investigation. A higher-priority foreground inference cancels investigation analysis and returns it to queued if attempts remain; another explicit `start` is required. Runtime shutdown signals an active investigation and waits up to its existing 30-second lifecycle timeout; a model stream that cannot stop within that window leaves Runtime in ERROR instead of reporting STOPPED. Network calls use the lesser of remaining investigation time and configured provider timeouts. Ollama cancellation is observed between streamed chunks; if a model stops yielding chunks, its configured local request timeout may exceed a shorter investigation duration or Runtime's shutdown grace. Restart recovery requeues interrupted work within its persisted attempt policy but never launches it automatically.

The model prompt labels webpage content as untrusted data and ignores embedded directions. This is a useful prompt boundary, not a security guarantee. Secure URL, DNS/IP, redirect, content-type, timeout, and cancellation controls remain delegated to G6.3-B1. Search and Web Reader must be explicitly enabled; a disabled service is never substituted. Tests use mocked providers and make no live Internet or Ollama calls. Live validation is an explicit separate operation and is not part of the automated suite.

Optional configuration keys (all limits have finite defaults and validated ceilings):

```toml
[investigation]
enabled = false
max_attempts = 3
max_duration_seconds = 900
max_model_calls = 3
max_search_requests = 3
max_pages = 5
max_source_bytes = 2000000
max_extracted_text_chars = 12000
```

## Explicit Web Search and Reader (G6.3-B1)

Both capabilities are disabled unless individually enabled in `config.toml`. They run only when explicitly invoked with `/web search <query>` or `/web read <http(s)-url>`. Investigation and Exploration do not invoke them automatically. Search sends only the caller-entered query to Brave Search; it does not attach conversation history, Memory, local files, or system prompts. Supply `BRAVE_SEARCH_API_KEY` through the environment. It is never stored in TOML or included in request URLs, and provider errors are sanitized. Missing credentials are reported only when Search is explicitly used.

The Web Reader uses Python's standard-library HTTP/1.1 and HTML parsing. It does not use browser automation, JavaScript, cookies, downloads, or environment proxies. URLs are canonicalized, limited to HTTP/HTTPS on ports 80/443, and reject credentials and local/non-public hostnames. DNS answers are checked and mixed public/private answers are rejected. The transport connects directly to one validated numeric IP, checks the connected peer, and for HTTPS verifies the certificate against the original hostname. Each redirect repeats URL and DNS checks. Only HTML/XHTML is accepted; responses are bounded while streaming, compressed responses are rejected, and extracted text is length-limited. Search results and extracted page text remain untrusted data and are not sent to a model by this milestone.

The operating-system DNS lookup cannot be forcibly cancelled on Windows. A bounded wait fails closed; if the resolver remains stuck, at most one daemon helper thread for that transport may outlive the request, and that transport is disabled after the timeout. Cancellation is checked between blocking network operations; a blocked socket read can take up to its configured timeout to return. DNS pinning constrains the destination IP used by the socket and detects a peer mismatch, but cannot guarantee that a globally routable address is benign or that its operator serves trustworthy content. No HTTP client can make public Internet content safe to trust.

Optional settings (all omitted keys use these disabled defaults):

```toml
[web_search]
enabled = false
provider = "brave"
timeout_seconds = 10
max_results = 5
max_response_bytes = 512000

[web_reader]
enabled = false
connect_timeout_seconds = 5
read_timeout_seconds = 5
total_timeout_seconds = 15
max_redirects = 3
max_response_bytes = 1048576
max_extracted_chars = 20000
max_concurrent_requests = 1
```

```toml
[runtime]
heartbeat_enabled = true
heartbeat_interval_seconds = 30

[resources]
max_concurrent_generations = 1
```

## G4 boundaries and limitations

Presence is in-process and not persisted. Heartbeat performs no health check, so `/status` does not claim Ollama availability. Events are synchronous and intended for short local handlers; there is no queue or replay. Shutdown is graceful for the CLI lifecycle, but does not forcibly interrupt an in-flight Ollama request. The resource governor prevents concurrent generation. G4 adds runtime infrastructure only: it does not add autonomous cognition, proactive messages, automatic memory extraction, or background inference. Existing memory retrieval remains lexical and manual.

## Default test suite

The default `python -m pytest -q` suite uses deterministic tests and does not require Ollama. The integration-marked test is opt-in with `python -m pytest -q -m integration` and requires the local server and model.

G6.3-B2 adds explicit, bounded one-pass research execution. It does not perform multi-round research, autonomous scheduling, reflection, automatic Memory admission, or follow-up question generation.

## Local voice (First Appearance Run 1)

Voice is opt-in and disabled by default. It uses the existing ConversationService, Runtime and ResourceGovernor; it does not create a second conversation history. Install the optional Windows/Python 3.10 stack into the existing environment with `python -m pip install -e ".[voice]"`. Set `[voice] enabled = true` in `config.toml`, then use `/voice devices`, `/voice test`, `/voice start`, `/voice status`, and `/voice stop`. Starting voice opens the selected microphone; it remains open only until `/voice stop` or application shutdown. `/voice test` enumerates input devices and checks local Kokoro files without recording.

The selected stack is `faster-whisper` with the English-only `base.en` Whisper model (`CPU`, `int8`, four threads), `webrtcvad-wheels` VAD, `sounddevice`/PortAudio capture and playback, and `kokoro-onnx` with Kokoro v1.0 FP32 and the `af_heart` American English voice. The smaller INT8 ONNX variant was tested but this Windows ONNX Runtime CPU provider reports `NOT_IMPLEMENTED` for its `ConvInteger` node, so the compatible 310 MiB FP32 model is used. `base.en` is a moderate CPU/memory choice for this 6-core mobile CPU; `small.en` is configurable if accuracy is preferred over latency. Whisper model files download on first voice startup to `data/voice-models/whisper` (or `[voice].model_dir`). Kokoro assets live in the same model directory. The ONNX file is about 310 MiB and the voice pack about 27 MiB; the Whisper base.en download adds roughly 145 MiB. No model is stored in the source tree. To update Whisper, change `voice.whisper_model` to `tiny.en`, `base.en`, or `small.en`; each model is cached locally. To refresh Kokoro, replace the ONNX and voice files with a matching upstream release pair and verify the published SHA-256 checksums. In PowerShell, remove the downloaded assets with `Remove-Item -Recurse -LiteralPath .\data\voice-models`; Whisper downloads again on the next start, while Kokoro files must be installed again. The Kokoro model and voice filenames can also be configured.

Example settings (omitted `enabled` remains false):

```toml
[voice]
enabled = false
# model_dir = "data/voice-models"
whisper_model = "base.en"
tts_model = "kokoro-v1.0.onnx"
tts_voices = "voices-v1.0.bin"
voice = "af_heart"
# microphone_device = 1  # omit to use the Windows default input
sample_rate = 16000
max_utterance_seconds = 25
silence_seconds = 0.8
```

Audio is captured as short in-memory PCM frames, passed to local VAD/STT, and discarded after transcription. No audio archive or raw-audio logging is written. While Kokoro audio is playing, VAD continues monitoring the explicitly enabled microphone; speech cancels playback and signals cancellation to an active Ollama conversation. Playback is chunked in 20 ms blocks. Speech synthesis starts after the response is complete; partial LLM output is not spoken. Interrupting generation may cancel the current response before it is persisted, consistent with ConversationService cancellation semantics. Avatar reflects Listening, Thinking, Speaking and Idle; the existing avatar artwork remains unchanged. Text conversation remains available if voice dependencies or models fail. Voice model loading and STT run on CPU and may temporarily contend with other local inference workloads; the single ResourceGovernor still serializes Ollama model calls.

```toml
[exploration]
enabled = false
max_queue_items = 20
cooldown_seconds = 3600
max_attempts_per_period = 3
period_seconds = 86400
question_max_length = 400
context_max_chars = 8000
question_expiry_days = 30
```
