"""Compare speech-to-text settings on your own recordings.

Run from backend/:
    python ../scripts/bench_stt.py [--models small small.en] [--hints on|off|both] [--beam 1 5] [-v]

Needs, in bench/audio/:  NAME.wav (16 kHz, mono, 16-bit) and NAME.txt (what you really said,
typed word for word).  Optional bench/terms.txt: one technical term per line.
Everything runs on this machine. Nothing is sent anywhere.
"""
import argparse
import asyncio
import gc
import json
import statistics
import sys
import time
import wave
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.providers.stt_faster_whisper import FasterWhisperSTT  # noqa: E402
from app.services.stt_eval import term_found, terms_in, wer_counts  # noqa: E402

GATE_SECONDS = 3.0  # Stage 0 gate: a projected 30 s answer should decode within about 3 s
DEFAULT_MODELS = ["small", "small.en", "distil-small.en", "base.en"]


def read_wav(path: Path) -> bytes:
    hint = (
        f"{path.name}: needs a 16 kHz, mono, 16-bit WAV. Convert it with:\n"
        f"  ffmpeg -i {path.name} -ac 1 -ar 16000 -sample_fmt s16 fixed.wav"
    )
    try:
        with wave.open(str(path), "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, 16000):
                raise SystemExit(hint)
            return w.readframes(w.getnframes())
    except wave.Error:
        raise SystemExit(hint) from None


def load_clips(audio_dir: Path) -> list[tuple[str, bytes, str]]:
    clips = []
    for wav in sorted(audio_dir.glob("*.wav")):
        ref = wav.with_suffix(".txt")
        if not ref.exists():
            print(f"skipping {wav.name}: no {ref.name} next to it")
            continue
        clips.append((wav.stem, read_wav(wav), ref.read_text(encoding="utf-8").strip()))
    if not clips:
        raise SystemExit(
            f"No usable clips in {audio_dir}. Put NAME.wav and NAME.txt pairs there "
            "(see the notes at the top of this script)."
        )
    return clips


def load_terms(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def rss_mb() -> float | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return None


async def run_config(model, hints_on, beam, args, clips, terms) -> dict:
    stt = FasterWhisperSTT(
        model=model,
        compute_type=args.compute_type,
        cpu_threads=args.threads,
        beam_size=beam,
        language=args.language,
        vad_filter=not args.no_vad,
    )
    t0 = time.perf_counter()
    await stt.warmup()
    load_s = time.perf_counter() - t0
    hints = terms if hints_on else None
    rows = []
    for name, pcm, ref in clips:
        started = time.perf_counter()
        result = await stt.transcribe(pcm, 16000, hints=hints)
        decode_s = time.perf_counter() - started
        edits, ref_words = wer_counts(ref, result.text)
        expected = terms_in(ref, terms)
        rows.append(
            {
                "clip": name,
                "audio_s": len(pcm) / 32000,
                "decode_s": decode_s,
                "edits": edits,
                "ref_words": ref_words,
                "terms_total": len(expected),
                "terms_strict": sum(term_found(t, result.text) for t in expected),
                "terms_lenient": sum(term_found(t, result.text, lenient=True) for t in expected),
                "reference": ref,
                "hypothesis": result.text,
            }
        )
    rss = rss_mb()
    del stt
    gc.collect()
    return {
        "model": model,
        "hints": hints_on,
        "beam": beam,
        "load_s": load_s,
        "rss_mb": rss,
        "clips": rows,
    }


def summarize(r: dict) -> dict:
    clips = r["clips"]
    words = sum(c["ref_words"] for c in clips)
    terms = sum(c["terms_total"] for c in clips)
    rtf = statistics.median(c["decode_s"] / c["audio_s"] for c in clips)
    return {
        "wer": sum(c["edits"] for c in clips) / words if words else None,
        "strict": sum(c["terms_strict"] for c in clips) / terms if terms else None,
        "lenient": sum(c["terms_lenient"] for c in clips) / terms if terms else None,
        "p50_s": statistics.median(c["decode_s"] for c in clips),
        "max_s": max(c["decode_s"] for c in clips),
        "rtf": rtf,
        "proj30": rtf * 30,
    }


def pct(x):
    return "-" if x is None else f"{x * 100:.1f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", type=Path, default=ROOT / "bench" / "audio")
    ap.add_argument("--terms", type=Path, default=ROOT / "bench" / "terms.txt")
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--hints", choices=["on", "off", "both"], default="both")
    ap.add_argument("--beam", type=int, nargs="+", default=[1])
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--threads", type=int, default=0, help="0 = library default; try $(nproc)")
    ap.add_argument("--language", default="en")
    ap.add_argument("--no-vad", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every clip")
    args = ap.parse_args()

    clips = load_clips(args.audio)
    terms = load_terms(args.terms)
    total_s = sum(len(pcm) / 32000 for _, pcm, _ in clips)
    print(f"{len(clips)} clips, {total_s:.0f} s of audio, {len(terms)} technical terms.")
    if not terms:
        print("No bench/terms.txt found: hints will be empty and term recall can't be scored.")
    print("Each model downloads from Hugging Face the first time it is used.\n")

    hint_modes = {"on": [True], "off": [False], "both": [False, True]}[args.hints]
    results = []
    for model in args.models:
        for beam in args.beam:
            for hints_on in hint_modes:
                label = f"{model} beam={beam} hints={'on' if hints_on else 'off'}"
                print(f"running {label} ...", flush=True)
                try:
                    results.append(
                        asyncio.run(run_config(model, hints_on, beam, args, clips, terms))
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  FAILED: {type(exc).__name__}: {str(exc)[:200]}")

    if not results:
        raise SystemExit("Nothing ran.")

    print()
    header = (
        f"{'config':34} {'WER':>6} {'terms':>6} {'lenient':>7} {'p50 s':>6} {'max s':>6} "
        f"{'RTF':>5} {'30s est':>7} {'load s':>6} {'RSS MB':>6}  gate"
    )
    print(header)
    for r in results:
        s = summarize(r)
        cfg = f"{r['model']} b{r['beam']} hints-{'on' if r['hints'] else 'off'}"
        rss = "-" if r["rss_mb"] is None else f"{r['rss_mb']:.0f}"
        gate = "ok" if s["proj30"] <= GATE_SECONDS else "SLOW"
        print(
            f"{cfg:34} {pct(s['wer']):>6} {pct(s['strict']):>6} {pct(s['lenient']):>7} "
            f"{s['p50_s']:>6.2f} {s['max_s']:>6.2f} {s['rtf']:>5.2f} {s['proj30']:>6.1f}s "
            f"{r['load_s']:>6.1f} {rss:>6}  {gate}"
        )
        if args.verbose:
            for c in r["clips"]:
                print(f"    [{c['clip']}] {c['audio_s']:.1f}s audio, {c['decode_s']:.2f}s decode")
                print(f"      said: {c['reference']}")
                print(f"      got : {c['hypothesis']}")

    print(
        f"\nWER = word error rate over all clips (lower is better). terms = share of your "
        f"listed terms transcribed exactly; lenient also accepts 'fast API' for FastAPI.\n"
        f"RTF = decode time / audio time, median over clips; '30s est' projects a 30 s answer "
        f"from it. gate: projected 30 s answer within {GATE_SECONDS:.0f} s.\n"
        f"RSS is the process size after decoding, approximate; run one model alone for a "
        f"clean figure. With a handful of clips these numbers are indicative, not statistics."
    )

    out_dir = ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"stt-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"details saved to {out}")


if __name__ == "__main__":
    main()