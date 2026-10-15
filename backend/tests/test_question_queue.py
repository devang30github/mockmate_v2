from app.domain.planning import PlanDraft, finalize_plan
from app.domain.question_queue import build_question_queue
from app.providers.mock.providers import CANNED_PLAN


def canned_plan():
    return finalize_plan(
        PlanDraft.model_validate(CANNED_PLAN),
        duration_minutes=30,
        difficulty="medium",
        role_title="ML Engineer",
    )


def test_queue_starts_with_the_opening_question():
    queue = build_question_queue(canned_plan())
    assert queue[0].kind == "opening" and queue[0].competency is None
    assert all(q.kind == "main" for q in queue[1:])
    assert len(queue) == 9


def test_first_round_visits_each_section_in_priority_order():
    plan = canned_plan()
    queue = build_question_queue(plan)
    assert [q.competency for q in queue[1:5]] == [s.competency for s in plan.sections]


def test_no_duplicate_questions():
    texts = [q.text for q in build_question_queue(canned_plan())]
    assert len(texts) == len(set(texts))


def test_section_quota_limits_how_many_questions_it_contributes():
    plan = canned_plan().model_copy(deep=True)
    plan.sections[0].target_questions = 1
    queue = build_question_queue(plan)
    assert len(queue) == 8
    assert sum(1 for q in queue if q.competency == plan.sections[0].competency) == 1