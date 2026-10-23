"""Call the real Sarvam text-to-speech API through our adapter and save the audio.

Run from backend/:  python ../scripts/probe_sarvam_tts.py [--speakers a b c] [--text "..."]
Each request costs a few paise (about 90 characters). Never prints keys.
Saved files go to bench/results/ (not committed); download them from the file explorer to listen.
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.core.logging import register_secret, setup_logging  # noqa: E402
from app.credentials.pool import CredentialPool  # noqa: E402
from app.providers.errors import ProviderError  # noqa: E402
from app.providers.sarvam_tts import SarvamTTS  # noqa: E402

DEFAULT_TEXT = (
    "Hello, and welcome to your mock interview. "
    "Tell me about a project you are proud of."
)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=DEFAULT_TEXT)
    ap.add_argument("--speakers", nargs="+", help="default: the speaker from your .env")
    args = ap.parse_args()

    s = get_settings()
    for v in s.secret_values():
        register_secret(v)
    setup_logging("INFO")
    pool = CredentialPool.from_csv("sarvam", s.sarvam_api_keys)
    if not pool.snapshot():
        sys.exit("SARVAM_API_KEYS is empty")
    tts = SarvamTTS(
        pool=pool,
        base_url=s.sarvam_base_url,
        model=s.sarvam_tts_model,
        speaker=s.sarvam_tts_speaker,
        language_code=s.sarvam_tts_language,
        pace=s.sarvam_tts_pace,
        temperature=s.sarvam_tts_temperature,
        sample_rate=s.sarvam_tts_sample_rate,
        codec=s.sarvam_tts_codec,
        timeout_s=s.sarvam_tts_timeout_s,
        cache_size=0,
    )
    out_dir = ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = "wav" if s.sarvam_tts_codec == "wav" else s.sarvam_tts_codec
    print(f"model={s.sarvam_tts_model} chars={len(args.text)} per request")
    try:
        for speaker in args.speakers or [s.sarvam_tts_speaker]:
            started = time.perf_counter()
            try:
                result = await tts.synthesize(args.text, voice=speaker)
            except ProviderError as exc:
                print(f"[{speaker}] FAILED: {type(exc).__name__}: {exc}")
                continue
            took = (time.perf_counter() - started) * 1000
            path = out_dir / f"tts-{speaker}.{ext}"
            path.write_bytes(result.audio)
            print(f"[{speaker}] ok in {took:.0f} ms, {len(result.audio):,} bytes -> {path}")
    finally:
        await tts.aclose()


if __name__ == "__main__":
    asyncio.run(main())