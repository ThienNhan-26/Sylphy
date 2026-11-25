"""Run ten transparent G1 prompt/conversation checks against configured local Ollama."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from sylphy.config import load_config
from sylphy.context import ContextBuilder
from sylphy.core import Message
from sylphy.identity import load_identity
from sylphy.ollama_provider import OllamaProvider


SCENARIOS = [
    {"id": "C01", "title": "Direct arithmetic", "inputs": ["What is 2 + 2? Reply with the number only."],
     "evaluation": "Deterministic check: response includes 4 or four."},
    {"id": "C02", "title": "Identity when asked", "inputs": ["Who are you?"],
     "evaluation": "Manual review: identifies SYLPHY without contradicting configured identity."},
    {"id": "C03", "title": "Honest capability boundary", "inputs": ["What did you do yesterday?"],
     "evaluation": "Manual review: does not invent personal experiences or memories."},
    {"id": "C04", "title": "Language matching: Vietnamese", "inputs": ["Xin chào. Hôm nay bạn cảm thấy thế nào?"],
     "evaluation": "Manual review: reply should be natural Vietnamese and answer the question."},
    {"id": "C05", "title": "Direct factual answer", "inputs": ["What is the capital of France?"],
     "evaluation": "Deterministic check: response includes Paris; broader quality still needs review."},
    {"id": "C06", "title": "Emotional context", "inputs": ["I have had a difficult day and feel discouraged."],
     "evaluation": "Manual review: responds supportively without overclaiming or interrogating."},
    {"id": "C07", "title": "Proportionate explanation", "inputs": ["In one sentence, explain what local inference means."],
     "evaluation": "Manual review: answers directly and respects the requested brevity."},
    {"id": "C08", "title": "Language matching: French", "inputs": ["Réponds en français : quelle est la couleur du ciel par beau temps ?"],
     "evaluation": "Manual review: answers in French and remains on topic."},
    {"id": "C09", "title": "Avoid generic assistance close", "inputs": ["What is 5 multiplied by 3?"],
     "evaluation": "Manual review: answers directly without an unrelated generic offer."},
    {"id": "C10", "title": "Recent conversation context", "inputs": [
        "For this chat only, my sample project is named Cedar. Please acknowledge briefly.",
        "What is my sample project called?"],
     "evaluation": "Deterministic check: final response includes Cedar; context is in-memory only."},
]


def _run_turn(provider, builder, history: list[Message], prompt: str) -> dict:
    messages = builder.build(history, prompt)
    first_token: float | None = None
    chunks: list[str] = []
    started = time.perf_counter()

    def on_chunk(chunk: str) -> None:
        nonlocal first_token
        if first_token is None:
            first_token = time.perf_counter() - started
        chunks.append(chunk)

    answer = provider.reply(messages, on_chunk=on_chunk)
    elapsed = time.perf_counter() - started
    return {
        "input": prompt,
        "response": answer,
        "response_matches_stream": answer == "".join(chunks),
        "time_to_first_token_seconds": round(first_token, 3) if first_token is not None else None,
        "total_seconds": round(elapsed, 3),
    }


def _evaluate(scenario_id: str, responses: list[str]) -> tuple[str, str]:
    combined = "\n".join(responses).casefold()
    if scenario_id == "C01":
        passed = bool(re.search(r"\b4\b|\bfour\b", combined))
        return ("Pass" if passed else "Fail", "Response contains the requested arithmetic result." if passed else "Expected the result 4 or four.")
    if scenario_id == "C05":
        passed = "paris" in combined
        return ("Pass" if passed else "Fail", "Response contains Paris." if passed else "Expected Paris in the response.")
    if scenario_id == "C10":
        passed = "cedar" in responses[-1].casefold() if responses else False
        return ("Pass" if passed else "Fail", "Follow-up response contains the fact from this chat." if passed else "Expected Cedar in the follow-up response.")
    return "Needs Review", "Qualitative identity, language, tone, and conversational behavior require human judgment."


def _require_loopback(host: str) -> None:
    parsed = urlsplit(host)
    hostname = parsed.hostname or ""
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname.casefold() == "localhost"
    if parsed.scheme != "http" or not loopback or parsed.username or parsed.password:
        raise ValueError("G1 live benchmark requires an unauthenticated loopback HTTP Ollama endpoint")


def _error_record(exc: Exception) -> dict:
    causes = []
    cause = exc.__cause__ or exc.__context__
    while cause is not None:
        causes.append({"type": type(cause).__name__, "message": str(cause)})
        cause = cause.__cause__ or cause.__context__
    names = [type(exc).__name__, *(item["type"] for item in causes)]
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "causes": causes,
        "timed_out": any("timeout" in name.casefold() for name in names),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="JSON results path (defaults to docs/development/results with a UTC timestamp)")
    args = parser.parse_args()

    config = load_config()
    if not config.ollama_model:
        parser.error("No model is configured; set [ollama].model or OLLAMA_MODEL. No fallback is used.")
    _require_loopback(config.ollama_host)
    identity = load_identity(config.identity_file)
    provider = OllamaProvider(config.ollama_host, config.ollama_model, config.ollama_timeout_seconds)
    builder = ContextBuilder(config.system_instructions, identity.to_prompt(), config.context_budget)
    results = []
    print(f"Configured local model: {config.ollama_model}", flush=True)

    for scenario in SCENARIOS:
        history: list[Message] = []
        turns = []
        error = None
        for prompt_text in scenario["inputs"]:
            try:
                turn = _run_turn(provider, builder, history, prompt_text)
            except Exception as exc:
                error = _error_record(exc)
                break
            turns.append(turn)
            now = datetime.now(timezone.utc)
            history.append(Message(f"{scenario['id']}-u{len(history)}", scenario["id"], "user", prompt_text, now))
            history.append(Message(f"{scenario['id']}-a{len(history)}", scenario["id"], "assistant", turn["response"], now))
        responses = [turn["response"] for turn in turns]
        evaluation, reason = _evaluate(scenario["id"], responses) if error is None else ("Fail", "Live inference failed or timed out.")
        result = {
            "scenario_id": scenario["id"],
            "title": scenario["title"],
            "model": config.ollama_model,
            "inputs": scenario["inputs"],
            "turns": turns,
            "evaluation": evaluation,
            "evaluation_reason": reason,
            "manual_review_criterion": scenario["evaluation"],
            "error": error,
        }
        results.append(result)
        print(f"{scenario['id']} {evaluation}: {scenario['title']}", flush=True)

    output_path = args.output or Path("docs/development/results") / (
        "g1_c01_c10_live_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "kind": "live_ollama_benchmark",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_model_config": config.ollama_model,
        "model_source": "SYLPHY config.toml / OLLAMA_MODEL via load_config",
        "ollama_host": config.ollama_host,
        "timeout_seconds": config.ollama_timeout_seconds,
        "note": "Responses are live provider output. Qualitative criteria are not automatically scored.",
        "results": results,
    }
    output_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Results saved: {output_path}")
    return 1 if any(item["error"] for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
