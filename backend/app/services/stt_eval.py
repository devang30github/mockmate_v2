import re

_KEEP = re.compile(r"[^a-z0-9'+#]+")


def normalize(text: str) -> str:
    """Lowercase and strip punctuation, keeping + and # so C++ and C# stay distinct from C."""
    lowered = text.lower().replace("\u2019", "'")
    return " ".join(_KEEP.sub(" ", lowered).split())


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def wer_counts(reference: str, hypothesis: str) -> tuple[int, int]:
    """(word errors, reference word count). Numbers are not normalized: '2M' vs
    'two million' counts as errors."""
    ref = normalize(reference).split()
    hyp = normalize(hypothesis).split()
    return edit_distance(ref, hyp), len(ref)


def wer(reference: str, hypothesis: str) -> float:
    edits, words = wer_counts(reference, hypothesis)
    if words == 0:
        return 0.0 if edits == 0 else 1.0
    return edits / words


def term_found(term: str, text: str, lenient: bool = False) -> bool:
    """Strict: the term appears as whole words. Lenient also ignores spaces, so 'fast API'
    counts for FastAPI (terms shorter than 4 letters stay strict to avoid false hits)."""
    t, x = normalize(term), normalize(text)
    if not t:
        return False
    if f" {t} " in f" {x} ":
        return True
    squashed = t.replace(" ", "")
    if lenient and len(squashed) >= 4:
        return squashed in x.replace(" ", "")
    return False


def terms_in(reference: str, terms: list[str]) -> list[str]:
    return [t for t in terms if term_found(t, reference)]