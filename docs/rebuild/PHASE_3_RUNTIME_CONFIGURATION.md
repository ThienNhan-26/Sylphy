# SYLPHY Phase 3 — Runtime Configuration Record

**Date:** 2026-11-27  
**Status:** Recorded from source inspection and Project Owner direction

## 1. Authoritative model and provider

| Setting | Intended value |
|---|---|
| Conversation model | Gemma 4 Artemis |
| Model file | `gemma-4-E2B-it-Artemis-Q3_K_M.gguf` |
| Conversation provider | `llama.cpp` |
| Cognition provider | `llama.cpp` |
| Default endpoint | `http://127.0.0.1:9931` |
| Conversation thinking | Enabled by default |
| Cognition thinking | Disabled by adapter request configuration |
| Cognition model | Inherits Conversation model unless a legacy override is explicitly supplied |

## 2. Runtime composition

The daemon creates one `asyncio.Semaphore(1)` and passes it to both the llama.cpp Conversation generator and `LlamaCppCognition`. This serializes local inference requests through the shared server.

Cognition is instantiated only when:
- an effective cognition model exists;
- autonomy is enabled; and
- an explicit sandbox root is supplied.

The provider instance is injected into `EntitySupervisor`; Entity Runtime remains the lifecycle and authority owner.

## 3. Legacy compatibility

Ollama remains present as a legacy compatibility option by explicit Project Owner decision (2026-11-28): keep the code, but do not use it in the default runtime.

Verified CLI defaults in the reviewed daemon source:
- `--conversation-provider`: `llama.cpp`.
- `--cognition-provider`: `llama.cpp`.

Ollama can only be selected explicitly through the legacy provider option; it is not an automatic fallback. The intended runtime does not require Ollama and does not require a separate `--cognition-model`.

**Phase 3 handling:** preserve the existing Ollama adapters and explicit legacy selection without further provider work. Do not benchmark, reactivate, or expand Ollama support. Revisit removal only through a separate Owner-approved decision.

## 4. Resource and performance evidence

Known evidence:
- Shared local inference concurrency: one request at a time.
- llama.cpp server endpoint: loopback `127.0.0.1:9931`.
- Live server context reported: 16896.
- One live Cognition completion was observed at approximately 10,968 ms.

Not established by the evidence reviewed:
- sustained tokens/second;
- peak RAM/VRAM/CPU utilization;
- latency distribution across repeated representative requests;
- performance under concurrent non-LLM workload.

The single observed completion time is an observation, not a benchmark or a guaranteed latency limit.

## 5. Configuration truthfulness

No claim is made that every Gemma capability, Vietnamese quality target, or hardware utilization threshold has been benchmarked. Model/provider comparison was excluded by Owner direction.
