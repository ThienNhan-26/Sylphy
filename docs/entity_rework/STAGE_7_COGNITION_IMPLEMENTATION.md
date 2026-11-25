# SYLPHY Stage 7 — Local LLM Cognition

**Status:** IN PROGRESS — provider adapter and Brain↔Runtime request/result path implemented; Windows live Ollama acceptance is the active gate. Stage 6 was accepted on Windows on 2026-11-24 (Python 3.13.17; 17 Stage 6 scenarios passed; complete Entity suite 167 passed, 2 skipped).

## Slice 1 — provider foundation

- `contracts/cognition.py`: bounded request, payload, evidence-reference, and outcome contracts.
- `cognition/interface.py`: provider-neutral capability boundary.
- `cognition/ollama.py`: loopback-only Ollama `/api/chat` adapter using structured JSON Schema output.
- Optional `cognition` extra adds `httpx`; the Entity base install does not require the capability.
- The model receives no tools and cannot create executable Runtime Intents or grant permissions.
- Request context is JSON-only and limited to 32 KiB; output is limited to 1,024 tokens and request timeout to 180 seconds.

## Slice 2 — Brain↔Runtime integration

### Request direction

1. Brain may emit a deterministic `CognitionRequestProposal` for a newly observed, known sandbox file revision when Runtime reports that the local cognition capability is configured.
2. The first vertical slice is deliberately narrow: summarize **filesystem metadata only**. It does not read or send file contents to the model.
3. Runtime reconstructs the request from the current World Snapshot and an explicit metadata allowlist (`relative_path`, `size_bytes`, `modified_ns`, `is_directory`, `kind`). Stale or missing source observations are rejected before provider dispatch.
4. Runtime appends a durable `cognition.requested` event before starting the local model call. Stable request IDs and event IDs prevent duplicate dispatch after replay.
5. Inference runs as a background task under an exclusive, capability-specific `llm:local:<model>` resource lease. The Entity event loop is not held open while the model responds.

### Result direction

1. Provider returns a typed `CognitionOutcome`; Runtime appends it as a durable `cognition.result` event.
2. Runtime converts the result into a pure `CognitionFeedback` and returns it to Brain.
3. Brain independently verifies that source observations are still `KNOWN` and every model citation belongs to the original request.
4. Accepted results are stored as `CognitionReceipt` in persistent Brain state. They remain evidence-bounded context only; they do not become Goal or Intent automatically.
5. Failed, invalid, unavailable, cancelled, or stale results are recorded as non-accepted receipts. Unmatched requests are reconciled on daemon restart; stale source data is not re-used.

### Operational safeguards

- `--cognition-model` is opt-in and requires `--autonomy` plus an explicit `--sandbox-root`.
- Ollama URL remains loopback-only.
- The provider itself also serializes inference through a single slot.
- Shutdown cancels in-flight cognition tasks and closes the provider. Unfinished read-only requests remain journaled for safe reconciliation.
- Brain imports no LLM, network, persistence, or Runtime modules.

## Windows usage

Install the optional capability:

```powershell
python -m pip install -e ".[cognition,dev]"
```

Start SYLPHY with an explicitly authorized sandbox and a local Ollama model:

```powershell
sylphy-entity --sandbox-root "D:\SYLPHY_SANDBOX" --autonomy --cognition-model "qwen3.5"
```

Use the exact Ollama model tag shown by `ollama list`. The endpoint can be overridden only with a loopback URL:

```powershell
sylphy-entity --sandbox-root "D:\SYLPHY_SANDBOX" --autonomy --cognition-model "qwen3.5" --cognition-base-url "http://127.0.0.1:11434"
```

Inspect `/status` (or `sylphy-entityctl status`) and confirm `cognition.enabled`, `cognition.inflight`, request/result counts, and Brain review receipts.

## Verification in this implementation environment

- Cognition provider tests: 9 passed.
- Brain cognition routing and provenance tests added.
- Persistence round-trip test added for request IDs and Brain review receipts.
- Runtime round-trip test added with a fake local provider. It verifies the request/result journal events, asynchronous execution, provenance review, and that file contents are not sent.
- Combined Entity suite: **178 passed**. Two deprecation warnings originate from the Uvicorn/websockets compatibility layer.

## Not yet claimed

- A real Ollama model has not yet been called from the user's Windows environment.
- Actual model quality, latency, resource usage, and cancellation behavior on the target local model remain to be accepted on Windows.
- Cognition integration does not complete all of Stage 7. Memory, Conversation, Vision/Hearing, Voice/Avatar, and CLI/UI integration remain in the order defined by the Master Plan.

## Slice 3 — Ollama structured-output compatibility and diagnostics

The first Windows live attempt reached `POST /api/chat` and received HTTP 200, but the provider rejected the model message as invalid structured output. The exact output failure category was not available in the original generic log, so this attempt is not accepted as a successful Cognition run.

Changes in this slice:

- Replaced the full Pydantic-generated wire schema (`$defs`/`$ref` and descriptive keywords) with a compact, flat JSON Schema for Ollama's constrained decoder.
- Removed the duplicated full output schema from the user prompt. The prompt now gives a short required-output shape; the `format` field remains the machine-readable schema.
- Set `think: false` for the structured-output turn so a thinking-capable model is asked for the final JSON payload directly.
- Kept Pydantic validation, bounds, extra-field rejection, and source-reference provenance checks as the final acceptance gate. The Ollama wire schema is not trusted as a substitute for application validation.
- Added narrow normalization for a single complete ```json fenced block. Free-form prose, partial JSON, wrong shapes, and unknown evidence references remain rejected.
- Replaced the generic malformed-output diagnostic with non-sensitive categories: `invalid_response_envelope`, `missing_message_content`, `empty_model_content`, `non_json_model_content`, `cognition_schema_mismatch`, and `unknown_evidence_reference`. Logs include only request ID, rejection category, and output byte count; model output and context are never logged.

Verification in the build environment:

- Cognition provider tests: 12 passed.
- Full Entity regression (runtime, world, brain, execution, persistence, cognition): **181 passed**.
- Two existing Uvicorn/websockets deprecation warnings remain; no test failures.

Live Windows acceptance remains pending. Re-run the exact Ollama test after applying this slice. The provider log will now identify the output failure category if the configured model still does not comply. An HTTP 200 response alone is not Cognition acceptance.


## Slice 4 — JSON mode for the verified local model

The user's Windows probe verified that `lfm3-uncensored-32k:latest` returns a valid JSON object with `format: "json"`, `done_reason: "stop"`, and an empty thinking field. The same model had returned non-JSON prose when invoked through the provider's JSON-Schema format.

Changes in this slice:

- The Ollama adapter now requests portable JSON mode (`format: "json"`) instead of JSON-Schema constrained decoding.
- Pydantic remains the authoritative output contract; wrong fields, missing required nested values, invalid confidence bounds, oversized values, and invalid evidence references remain rejected.
- The provider does not try to salvage arbitrary prose or silently convert model text into evidence. A JSON mode response is accepted only after full contract and provenance validation.
- Provider tests now assert the exact wire format.

Evidence from the user's Windows probe:

- Ollama model: `lfm3-uncensored-32k:latest`.
- `POST /api/chat` returned successfully.
- `done_reason` was `stop`.
- `message.content` was valid JSON matching the top-level Cognition shape with empty findings/proposals.
- `message.thinking` was empty.

This verifies JSON mode compatibility, not yet full runtime Cognition acceptance. A new live run must show a COMPLETED provider result and a Brain review receipt.


## Slice 5 — Windows live acceptance harness

Added `tools/verify_entity_stage7_cognition_windows.ps1`. It starts an isolated Entity Daemon with an explicit temporary sandbox and the exact user-supplied Ollama model tag, creates a probe file after the daemon is healthy, and polls the loopback `/status` endpoint. Acceptance requires all of the following:

- Cognition is enabled in the running daemon.
- Brain originated at least one request (`requested >= 1`).
- The local provider completed at least one request (`completed >= 1`).
- Brain reviewed the result (`reviewed >= 1`).
- A recent review reports `status=completed` and `accepted_as_evidence=true`.

The script shuts down the daemon through its authenticated local endpoint and retains stdout/stderr logs under `logs/`. It does not treat HTTP 200, a provider-only response, or a fake-provider unit test as live acceptance.

Run from the repository root in PowerShell:

```powershell
py -3.13 -m pip install -e ".[cognition,dev,world]"
ollama list
powershell -ExecutionPolicy Bypass -File .\tools\verify_entity_stage7_cognition_windows.ps1 -Model "EXACT_MODEL_TAG_FROM_OLLAMA_LIST"
```

The exact model tag must be supplied by the user from `ollama list`; the script does not guess or rewrite it. A successful run prints `PASS: Stage 7 Slice 1 Windows live acceptance completed`. Until that evidence exists, Slice 1 remains in progress and the plan does not advance to Memory.
