"""Reproducible, opt-in benchmark for locally installed Ollama models."""

import argparse
import json
import time
from typing import Any

from ollama import Client

from .config import load_config


def run_benchmark(host: str, models: list[str], sizes: list[int], timeout: float) -> list[dict[str, Any]]:
    client = Client(host=host, timeout=timeout)
    installed = {item.model for item in client.list().models}
    results: list[dict[str, Any]] = []
    for model in models:
        for size in sizes:
            prompt = ("Context benchmark data. " * ((size * 4 // 25) + 1))[:size * 4]
            started = time.perf_counter()
            first_token: float | None = None
            final: Any = None
            try:
                if model not in installed:
                    raise ValueError("model is not installed locally")
                stream = client.chat(model=model, messages=[{"role": "user", "content": prompt + " Reply briefly."}], stream=True,
                                     options={"num_predict": 64})
                for chunk in stream:
                    visible = chunk.message.content or ""
                    thinking = getattr(chunk.message, "thinking", None) or ""
                    if first_token is None and (visible or thinking):
                        first_token = time.perf_counter() - started
                    final = chunk
                elapsed = time.perf_counter() - started
                generated = int(final.eval_count or 0) if final else 0
                results.append({
                    "model": model,
                    "requested_context_tokens": size,
                    "prompt_tokens": int(final.prompt_eval_count or 0) if final else None,
                    "time_to_first_token_seconds": first_token,
                    "generation_tokens_per_second": round(generated / (int(final.eval_duration) / 1e9), 3)
                    if final and final.eval_duration else None,
                    "total_response_seconds": round(elapsed, 3),
                    "generated_tokens": generated,
                    "error": None,
                })
            except Exception as exc:
                results.append({"model": model, "requested_context_tokens": size,
                                "time_to_first_token_seconds": first_token,
                                "total_response_seconds": round(time.perf_counter() - started, 3),
                                "error": f"{type(exc).__name__}: {exc}"})
    return results


def main() -> None:
    config = load_config()
    parser = argparse.ArgumentParser(description="Benchmark selected locally installed Ollama models")
    parser.add_argument("--models", nargs="+", default=[config.ollama_model])
    parser.add_argument("--contexts", nargs="+", type=int, default=[256, 1024, 4096])
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    print(json.dumps(run_benchmark(config.ollama_host, args.models, args.contexts, args.timeout), indent=2))


if __name__ == "__main__":
    main()
