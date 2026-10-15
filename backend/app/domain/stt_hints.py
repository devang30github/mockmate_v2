import re

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SOURCES = (  # most distinctive proper nouns first
    ("profile", "technologies"),
    ("profile", "skills"),
    ("job", "required_skills"),
    ("job", "preferred_skills"),
)
MAX_TERM_CHARS = 40
PROMPT_PREFIX = "Technical job interview. Terms: "


def _clean(term: str) -> str:
    return " ".join(_CONTROL.sub(" ", term).split())


def extract_terms(analysis: dict, limit: int = 40) -> list[str]:
    """Technical terms from a stored analysis, de-duplicated, for speech-recognition hints."""
    seen: set[str] = set()
    out: list[str] = []
    for section, field in _SOURCES:
        block = analysis.get(section)
        values = block.get(field) if isinstance(block, dict) else None
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            term = _clean(value)
            if not term or len(term) > MAX_TERM_CHARS:
                continue
            lowered = term.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(term)
            if len(out) >= limit:
                return out
    return out


def build_initial_prompt(terms: list[str], max_chars: int = 500) -> str | None:
    """A short vocabulary prompt for Whisper, or None when there is nothing to say."""
    chosen: list[str] = []
    used = len(PROMPT_PREFIX) + 1  # the closing period
    for raw in terms:
        term = _clean(raw)
        if not term:
            continue
        cost = len(term) + (2 if chosen else 0)  # ", " between terms
        if used + cost > max_chars:
            break
        chosen.append(term)
        used += cost
    if not chosen:
        return None
    return PROMPT_PREFIX + ", ".join(chosen) + "."