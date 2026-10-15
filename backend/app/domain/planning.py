import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field

Category = Literal[
    "technical_depth",
    "system_design",
    "problem_solving",
    "resume_deep_dive",
    "behavioral",
    "role_fit",
]
Depth = Literal["basic", "intermediate", "advanced"]

# Planning defaults (assumptions to be tuned from real sessions, not measured values).
MINUTES_PER_QUESTION = {"easy": 2.5, "medium": 3.0, "hard": 3.5}
MAX_QUESTION_CHARS = 400
DUP_THRESHOLD = 0.8
DEFAULT_FOLLOW_UP_POLICY = (
    "Ask a follow-up when an answer is vague, unsupported, or skips the trade-offs. "
    "Move on after two follow-ups."
)


class PlanInvalid(Exception):
    pass


def _clip(n: int):
    def inner(v):
        if v is None:
            return []
        if isinstance(v, list):
            return v[:n]
        return v

    return inner


def _weight(v):
    try:
        return min(10, max(1, round(float(v))))
    except (TypeError, ValueError):
        raise ValueError("weight must be a number") from None


class PlanSectionDraft(BaseModel):
    competency: str = Field(min_length=2, max_length=120)
    category: Category
    rationale: str = ""
    weight: Annotated[int, BeforeValidator(_weight)]
    expected_depth: Depth = "intermediate"
    seed_questions: Annotated[list[str], BeforeValidator(_clip(4)), Field(min_length=1)]
    source_claims: Annotated[list[str], BeforeValidator(_clip(5))] = Field(default_factory=list)
    follow_up_strategy: str = ""


class PlanDraft(BaseModel):
    """What the LLM returns. Minutes and counts are computed by code, never by the model."""

    opening_question: str = ""
    sections: Annotated[
        list[PlanSectionDraft], BeforeValidator(_clip(12)), Field(min_length=1)
    ]
    follow_up_policy: str = ""


class PlanSection(PlanSectionDraft):
    minutes: float
    target_questions: int


class PlanBudget(BaseModel):
    duration_minutes: int
    reserved_minutes: float
    usable_minutes: float
    minutes_per_question: float
    total_questions: int
    max_sections: int


class PlanConstraints(BaseModel):
    no_repeat_questions: bool = True
    adapt_to_remaining_time: bool = True
    max_followups_per_question: int = 2
    min_questions_per_section: int = 1
    wrap_up_reserve_minutes: float


class Plan(BaseModel):
    opening_question: str
    sections: list[PlanSection]  # priority order: highest weight first
    follow_up_policy: str
    budget: PlanBudget
    constraints: PlanConstraints


def make_budget(duration_minutes: int, difficulty: str) -> PlanBudget:
    reserved = 1.0 if duration_minutes <= 15 else 2.0
    usable = duration_minutes - reserved
    per_question = MINUTES_PER_QUESTION[difficulty]
    total = max(3, round(usable / per_question))
    return PlanBudget(
        duration_minutes=duration_minutes,
        reserved_minutes=reserved,
        usable_minutes=usable,
        minutes_per_question=per_question,
        total_questions=total,
        max_sections=min(8, max(2, total // 2)),
    )


def allocate(weights: list[int], total: int, minimum: int = 1) -> list[int]:
    """Split `total` items across weights (largest remainder). Sums exactly to `total`."""
    n = len(weights)
    if n == 0:
        return []
    if total < minimum * n:
        raise ValueError("total is too small for the minimum per item")
    remaining = total - minimum * n
    wsum = sum(weights)
    raw = [remaining * w / wsum for w in weights]
    base = [int(r) for r in raw]
    leftover = remaining - sum(base)
    for i in sorted(range(n), key=lambda i: (-(raw[i] - base[i]), i))[:leftover]:
        base[i] += 1
    return [minimum + b for b in base]


_WORD = re.compile(r"[a-z0-9+#.]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def finalize_plan(
    draft: PlanDraft, *, duration_minutes: int, difficulty: str, role_title: str
) -> Plan:
    budget = make_budget(duration_minutes, difficulty)

    # Highest-weight sections claim questions first, so a low-priority section
    # can never block a better one through de-duplication.
    ordered = sorted(enumerate(draft.sections), key=lambda p: (-p[1].weight, p[0]))
    seen_names: set[str] = set()
    seen_tokens: list[set[str]] = []
    cleaned: list[tuple[PlanSectionDraft, list[str]]] = []
    for _, section in ordered:
        if len(cleaned) >= budget.max_sections:
            break
        name = " ".join(section.competency.lower().split())
        if name in seen_names:
            continue
        questions: list[str] = []
        for q in section.seed_questions:
            q = " ".join(q.split())
            if not q or len(q) > MAX_QUESTION_CHARS:
                continue
            toks = _tokens(q)
            if any(_jaccard(toks, other) >= DUP_THRESHOLD for other in seen_tokens):
                continue
            seen_tokens.append(toks)
            questions.append(q)
        if not questions:
            continue
        seen_names.add(name)
        cleaned.append((section, questions))

    if len(cleaned) < 2:
        raise PlanInvalid("plan has fewer than 2 usable sections")

    counts = allocate([s.weight for s, _ in cleaned], budget.total_questions)
    sections = [
        PlanSection(
            **{**s.model_dump(), "seed_questions": questions},
            minutes=round(budget.usable_minutes * n / budget.total_questions, 1),
            target_questions=n,
        )
        for (s, questions), n in zip(cleaned, counts)
    ]

    opening = " ".join(draft.opening_question.split())
    if not opening or len(opening) > MAX_QUESTION_CHARS:
        opening = (
            "Thanks for joining. To start, could you give me a short overview of your "
            f"background and what draws you to the {role_title} role?"
        )
    return Plan(
        opening_question=opening,
        sections=sections,
        follow_up_policy=" ".join(draft.follow_up_policy.split()) or DEFAULT_FOLLOW_UP_POLICY,
        budget=budget,
        constraints=PlanConstraints(wrap_up_reserve_minutes=budget.reserved_minutes),
    )