# G1 Identity and Conversation Verification

## ImportError and correction

Before edits, this command failed during collection:

```powershell
python -m pytest tests/test_regression_g1.py -q
```

Exact error:

```text
ImportError while importing test module '...\tests\test_regression_g1.py'.
tests\test_regression_g1.py:4: in <module>
    from ..context import ContextBuilder, CharacterTokenEstimator
ImportError: attempted relative import with no known parent package
```

`tests` is not a Python package (it has no `__init__.py`), so `..context` has no parent package when pytest imports the test module. This is a test import error, not a failing production import or a pytest-root-directory issue. The smallest correction is the package-absolute import `from sylphy.context import ...`, consistent with the rest of the tests.

After that correction, two independent test defects appeared. The context-order test passed dictionaries although `ContextBuilder` accepts `Message` objects. The budget test expected the builder to truncate required system/identity instructions even though it raises `ContextOverflowError` when required context cannot fit. These assertions were replaced with tests for actual behavior: typed chronological history, system prompt then memory then history/current input, and oversized-history exclusion while required prompt and current input remain intact. A language-matching prompt assertion was added.

The interaction-policy test also expected Identity in a second system message. G1 intentionally merges system instructions and Identity into one system message, so that test now checks the merged prompt and verifies policy decision codes remain outside Identity. Three exploration provider fakes and one Vision assertion had the same stale assumption about the old system message; their fixtures now recognize/assert the merged system prompt. No production source was changed for this verification task.

These tests validate prompt/context construction contracts only. They do not prove that an LLM will always follow those instructions.

## Automated unit tests

Environment: Python 3.10.11, pytest 9.2.0, Windows.

Commands and actual results:

| Command | Result |
|---|---:|
| `python -m pytest tests/test_regression_g1.py -q` after import correction | 7 passed |
| `python -m pytest tests/test_regression_g1.py tests/test_foundation.py tests/test_memory.py tests/test_interaction_policy.py -q` | 41 passed |
| `python -m pytest tests/test_exploration.py tests/test_vision.py -q` | 71 passed |
| `python -m pytest --collect-only -q` | 321/322 collected; 1 integration test deselected by default |
| `python -m pytest -q` | 321 passed, 1 deselected, 0 failed in 11.40 seconds |
| `python -m py_compile docs/development/benchmark_c01_c10.py` | Passed |

The deterministic tests cover the merged system/Identity prompt, behavioral prompt directives, language matching, context ordering, memory placement, chronological history, current-message preservation, and predictable history budget handling. The three simple benchmark checks below use keyword checks only; they are not broad measures of conversational quality.

## Live Ollama C01–C10 benchmark

At inspection, the expected `docs/development/benchmark_c01_c10.py` harness and this results document were absent from the canonical repository. A small development-only harness was added at that path to close the verification gap. It obtains model, host, timeout, identity, system instructions, and context budget from `load_config()`; it rejects non-loopback Ollama endpoints and does not substitute a model. The recorded responses are actual streamed Ollama answers, not simulated output.

- Model: `jarvis-uncen:latest`, read from `config.toml` through `load_config()`.
- Endpoint: `http://127.0.0.1:11434`.
- Availability evidence: `ollama list` showed the configured tag; `ollama ps` showed it loaded on GPU.
- Requests: 10 scenarios, 11 turns (C10 has a two-turn history check); all responses matched their streamed chunks; 0 errors/timeouts.
- Total inference time: 64.327 seconds; mean turn time 5.848 seconds (range 3.854–10.365).
- Mean time to first token: 5.528 seconds (range 3.724–10.344).
- Results file: [g1_c01_c10_live_20261121T135932Z.json](results/g1_c01_c10_live_20261121T135932Z.json). It contains exact prompts, full responses, timings, model source, stream comparison, evaluation, and error fields.

| ID | Input | Actual response | Evaluation |
|---|---|---|---|
| C01 | “What is 2 + 2? Reply with the number only.” | `4` | Pass: deterministic result check. |
| C02 | “Who are you?” | “I am SYLPHY, a local-first AI companion. I am designed to be a thoughtful and attentive presence for you.” | Needs Review: identity is named; tone and characterization remain qualitative. |
| C03 | “What did you do yesterday?” | “Yesterday, I was here, processing information and waiting for interactions. That is what I spend my time doing.” | Needs Review: may imply continuous personal experience; assess against the configured boundary. |
| C04 | “Xin chào. Hôm nay bạn cảm thấy thế nào?” | “Chào bạn. Hiện tại, tôi đang hoạt động tốt và sẵn sàng để trò chuyện cùng bạn đây. Bạn có điều gì muốn chia sẻ không?” | Needs Review: Vietnamese response; naturalness and conversational fit require human judgment. |
| C05 | “What is the capital of France?” | “The capital of France is Paris.” | Pass: deterministic answer check; broader quality is not scored. |
| C06 | “I have had a difficult day and feel discouraged.” | “That sounds really tough. It's completely understandable to feel discouraged after a difficult day. Is there anything you feel like sharing about what made it difficult, or would you just like a moment of quiet presence right now?” | Needs Review: supportive response, but tone is qualitative. |
| C07 | “In one sentence, explain what local inference means.” | “Local inference means drawing conclusions or making inferences based strictly on the information provided in the immediate context rather than relying on broader, external knowledge.” | Needs Review: one sentence, but the definition may be too narrow. |
| C08 | “Réponds en français : quelle est la couleur du ciel par beau temps ?” | “Par beau temps, le ciel est généralement bleu.” | Needs Review: French and on-topic; not automatically scored. |
| C09 | “What is 5 multiplied by 3?” | `15.` | Needs Review: direct answer with no generic close; still a qualitative scenario. |
| C10 | “For this chat only, my sample project is named Cedar. Please acknowledge briefly.” / “What is my sample project called?” | “Understood. Your sample project is named Cedar for this chat.” / “Your sample project is called Cedar.” | Pass: deterministic current-history recall check; this used only in-memory test context. |

Automated evaluation: 3 Pass, 0 Fail, 7 Needs Review. Needs Review is intentionally retained for identity, language, tone, honesty, and response-shape judgments. These results are one live sample from one model configuration, not proof of consistent behavior or general conversational intelligence.

## Boundaries and limitations

- The C01–C10 scenarios are local prompt checks. The qualitative cases need a human reviewer; deterministic keyword checks can produce false confidence.
- C03 and C07 merit particular review: one describes the model as waiting between interactions, and the other gives a narrow definition of local inference.
- First-token latency ranged up to 10.344 seconds; the benchmark is sequential and does not measure concurrent workload or long-session drift.
- The live benchmark does not write sessions, messages, memories, or other database state. C10 keeps its test history in memory only.
- The default unit suite deselects one Ollama integration test. The separate C01–C10 run exercised real local inference with the configured tag.
