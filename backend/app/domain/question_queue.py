from dataclasses import dataclass

from app.domain.planning import Plan


@dataclass(frozen=True)
class QueuedQuestion:
    kind: str  # "opening" | "main"
    text: str
    competency: str | None


def build_question_queue(plan: Plan | dict) -> list[QueuedQuestion]:
    """The ordered questions for a session: the opening question, then seed questions
    round-robin across sections in priority order, within each section's quota.

    Question number n (1-based) is queue[n - 1]. This is the fixed, non-adaptive
    ordering used until the LLM-driven interviewer arrives in Stage 5.
    """
    p = plan if isinstance(plan, Plan) else Plan.model_validate(plan)
    queue = [QueuedQuestion("opening", p.opening_question, None)]
    limits = [min(s.target_questions, len(s.seed_questions)) for s in p.sections]
    taken = [0] * len(p.sections)
    progressed = True
    while progressed:
        progressed = False
        for i, section in enumerate(p.sections):
            if taken[i] < limits[i]:
                queue.append(
                    QueuedQuestion("main", section.seed_questions[taken[i]], section.competency)
                )
                taken[i] += 1
                progressed = True
    return queue