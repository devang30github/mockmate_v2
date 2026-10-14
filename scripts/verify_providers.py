"""Probe real provider endpoints. Run from backend/:  python ../scripts/verify_providers.py

Sends a few tiny real requests (one /models call and one 5-token completion per
APINex key). Never prints keys. Needs backend/.env with your settings.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.logging import redact, register_secret  # noqa: E402


def keys_from(csv: str) -> list[str]:
    return [k.strip() for k in csv.split(",") if k.strip()]


def ratelimit_headers(resp: httpx.Response) -> dict:
    return {
        k: v
        for k, v in resp.headers.items()
        if k.lower().startswith("x-ratelimit") or k.lower() == "retry-after"
    }


async def check_apinex(s) -> None:
    keys = keys_from(s.apinex_api_keys)
    print("== APINex ==")
    if not keys:
        print("SKIP: APINEX_API_KEYS is empty")
        return
    base = s.apinex_base_url.rstrip("/")
    print(f"base_url={base}  configured_model={s.apinex_model or '(not set)'}")
    async with httpx.AsyncClient(timeout=30) as client:
        for i, key in enumerate(keys, start=1):
            label = f"apinex_key_{i:02d}"
            headers = {"Authorization": f"Bearer {key}"}
            try:
                r = await client.get(f"{base}/models", headers=headers)
                print(f"[{label}] GET /models -> {r.status_code}")
                if r.status_code == 200:
                    ids = [m.get("id") for m in r.json().get("data", [])]
                    print(f"[{label}] {len(ids)} models; first 15: {ids[:15]}")
                    if s.apinex_model:
                        print(f"[{label}] configured model listed: {s.apinex_model in ids}")
                print(f"[{label}] rate-limit headers: {ratelimit_headers(r) or 'none'}")
                if s.apinex_model and r.status_code == 200:
                    t0 = time.perf_counter()
                    c = await client.post(
                        f"{base}/chat/completions",
                        headers=headers,
                        json={
                            "model": s.apinex_model,
                            "messages": [{"role": "user", "content": "Reply with the word ok."}],
                            "max_tokens": 5,
                        },
                    )
                    ms = (time.perf_counter() - t0) * 1000
                    print(f"[{label}] POST /chat/completions -> {c.status_code} in {ms:.0f} ms")
                    if c.status_code == 200:
                        body = c.json()
                        print(f"[{label}] usage reported: {body.get('usage')}")
                    else:
                        print(f"[{label}] error body: {redact(c.text[:300])}")
            except Exception as exc:  # noqa: BLE001
                print(f"[{label}] FAILED: {type(exc).__name__}: {redact(str(exc))[:200]}")


async def check_sarvam(s) -> None:
    print("== Sarvam ==")
    keys = keys_from(s.sarvam_api_keys)
    print(f"keys configured: {len(keys)}")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(s.sarvam_base_url)
        print(f"reachability {s.sarvam_base_url} -> HTTP {r.status_code} (DNS + TLS ok)")
    except Exception as exc:  # noqa: BLE001
        print(f"reachability FAILED: {type(exc).__name__}: {redact(str(exc))[:200]}")
    print(
        "NOTE: reachability only. STT/TTS endpoints, auth header, request and response "
        "formats, models and pricing must be confirmed against current Sarvam docs "
        "before the Stage 3 adapters are written."
    )


async def main() -> None:
    s = get_settings()
    for v in s.secret_values():
        register_secret(v)
    await check_apinex(s)
    print()
    await check_sarvam(s)


if __name__ == "__main__":
    asyncio.run(main())