"""Benchmark candidate models with the real adapter.

Run from backend/:
    python ../scripts/bench_llm.py [--runs 3] [--json-mode prompt|native] [model ...]

Sends about 10 real requests per model (small, sequential). Uses a synthetic
resume, never yours. Never prints keys.
"""
import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings  # noqa: E402
from app.core.logging import register_secret, setup_logging  # noqa: E402
from app.credentials.pool import CredentialPool  # noqa: E402
from app.domain.analysis import Analysis  # noqa: E402
from app.prompts.analysis import build_messages  # noqa: E402
from app.providers.base import LLMMessage  # noqa: E402
from app.providers.errors import PaymentRequired, ProviderError  # noqa: E402
from app.providers.ratelimit import RateLimiter  # noqa: E402
from app.providers.llm_openai_compat import OpenAICompatLLM  # noqa: E402
from app.services.structured import StructuredOutputError, complete_structured  # noqa: E402
from tests.factories import SAMPLE_JD, SAMPLE_RESUME  # noqa: E402



DEFAULT_MODELS = [
    "free/gpt-6-luna",
    "free/gemini-3.8-flash",
    "free/glm-5.3-flash",
    "free/deepseek-v4.1-flash",
]
STREAM_PROMPT = [
    LLMMessage("system", "You are a technical interviewer."),
    LLMMessage(
        "user",
        "Ask one short follow-up question about trade-offs when scaling a FastAPI "
        "service. One sentence only.",
    ),
]


def p50(xs):
    return statistics.median(xs) if xs else None


def fmt(x):
    return "-" if x is None else f"{x:,.0f}"


async def bench_model(model, s, runs, json_style,limiter):
    pool = CredentialPool.from_csv("apinex", s.apinex_api_keys)
    llm = OpenAICompatLLM(
        base_url=s.apinex_base_url,
        model=model,
        pool=pool,
        timeout_s=s.apinex_timeout_s,
        max_attempts=1,  # no retries: failures should show up, not be masked
        json_style=json_style,
        limiter=limiter,
    )
    errors: list[str] = []
    tiny_ms, reasoning, completion, ttft, total, json_ms = [], [], [], [], [], []
    json_ok = 0
    json_n = max(1, runs - 1)

    for _ in range(runs):
        try:
            r = await llm.complete(
                [LLMMessage("user", "Reply with the single word: ok")], max_tokens=20
            )
            tiny_ms.append(r.latency_ms)
            completion.append(r.completion_tokens or 0)
            reasoning.append(r.reasoning_tokens or 0)
        except ProviderError as e:
            errors.append(f"tiny:{type(e).__name__}")

    for _ in range(runs):
        t0 = time.perf_counter()
        first = None
        try:
            async for _chunk in llm.stream(STREAM_PROMPT, max_tokens=120):
                if first is None:
                    first = (time.perf_counter() - t0) * 1000
            if first is None:
                errors.append("stream:no_content")
            else:
                ttft.append(first)
                total.append((time.perf_counter() - t0) * 1000)
        except ProviderError as e:
            errors.append(f"stream:{type(e).__name__}")

    messages = build_messages("\n".join(SAMPLE_RESUME), SAMPLE_JD, "Machine Learning Engineer")
    for _ in range(json_n):
        t0 = time.perf_counter()
        try:
            await complete_structured(llm, messages, Analysis, max_tokens=3500)
            json_ok += 1
            json_ms.append((time.perf_counter() - t0) * 1000)
        except StructuredOutputError:
            errors.append("json:invalid_output")
        except ProviderError as e:
            errors.append(f"json:{type(e).__name__}")

    await llm.aclose()
    return {
        "model": model, "tiny_ms": tiny_ms, "reasoning": reasoning, "completion": completion,
        "ttft": ttft, "total": total, "json_ok": json_ok, "json_n": json_n,
        "json_ms": json_ms, "errors": errors,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--json-mode", choices=["prompt", "native"], default="prompt")
    ap.add_argument("--rpm", type=int, default=5)
    args = ap.parse_args()

    s = get_settings()
    for v in s.secret_values():
        register_secret(v)
    setup_logging("WARNING")
    if not s.apinex_api_keys.strip():
        sys.exit("APINEX_API_KEYS is empty")

    limiter = RateLimiter(args.rpm)
    models = args.models or DEFAULT_MODELS
    probe = OpenAICompatLLM(
        base_url=s.apinex_base_url,
        model=models[0],
        pool=CredentialPool.from_csv("apinex", s.apinex_api_keys),
        max_attempts=1,
        limiter=limiter,
    )
    try:
        await probe.complete([LLMMessage("user", "Reply: ok")], max_tokens=20)
    except PaymentRequired:
        sys.exit("APINex says payment or check-in is required. Complete your check-in, then re-run.")
    except ProviderError as e:
        sys.exit(f"preflight failed: {type(e).__name__}")
    finally:
        await probe.aclose()

    rows = []
    for model in models:
        print(f"benchmarking {model} ...", flush=True)
        rows.append(await bench_model(model, s, args.runs, args.json_mode, limiter))

    print()
    print(
        f"{'model':28} {'tiny ms':>8} {'reason tok':>10} {'out tok*':>8} "
        f"{'TTFT ms':>8} {'stream ms':>9} {'JSON ok':>8} {'JSON ms':>8}"
    )
    for r in rows:
        ok = f"{r['json_ok']}/{r['json_n']}"
        print(
            f"{r['model'][:28]:28} {fmt(p50(r['tiny_ms'])):>8} {fmt(p50(r['reasoning'])):>10} "
            f"{fmt(p50(r['completion'])):>8} {fmt(p50(r['ttft'])):>8} "
            f"{fmt(p50(r['total'])):>9} {ok:>8} {fmt(p50(r['json_ms'])):>8}"
        )
        if r["errors"]:
            print(f"    errors ({len(r['errors'])}): {sorted(set(r['errors']))}")
    print(
        f"\nMedians over {args.runs} runs (JSON: {max(1, args.runs - 1)}), json-mode={args.json_mode}."
        "\n* out tok: completion tokens for a request capped at max_tokens=20."
        "\nSmall samples: treat as indicative, not statistics."
    )


if __name__ == "__main__":
    asyncio.run(main())