import pytest

from app.domain.planning import (
    PlanDraft,
    PlanInvalid,
    PlanSectionDraft,
    allocate,
    finalize_plan,
    make_budget,
)
from app.providers.mock.providers import CANNED_PLAN


def sec(name, weight, questions, category="technical_depth"):
    return {
        "competency": name,
        "category": category,
        "weight": weight,
        "seed_questions": questions,
    }


def draft(sections, opening="Tell me about yourself."):
    return PlanDraft.model_validate({"opening_question": opening, "sections": sections})


def finalize(d, minutes=30, difficulty="medium"):
    return finalize_plan(d, duration_minutes=minutes, difficulty=difficulty, role_title="ML Engineer")


def test_budget_30_minutes_medium():
    b = make_budget(30, "medium")
    assert (b.usable_minutes, b.total_questions, b.max_sections) == (28.0, 9, 4)


def test_budget_15_minutes_easy():
    b = make_budget(15, "easy")
    assert (b.usable_minutes, b.total_questions, b.max_sections) == (14.0, 6, 3)


def test_budget_60_minutes_hard():
    b = make_budget(60, "hard")
    assert (b.total_questions, b.max_sections) == (17, 8)


def test_allocate_sums_exactly_with_largest_remainder():
    assert allocate([9, 8, 7, 6], 9) == [3, 2, 2, 2]


def test_allocate_favors_heavier_weights_and_keeps_minimum():
    result = allocate([10, 1], 10)
    assert sum(result) == 10
    assert result[0] > result[1] >= 1


def test_duplicate_questions_are_removed_across_sections():
    d = draft(
        [
            sec("API design", 9, [
                "How would you design a FastAPI service for high traffic?",
                "Tell me about a time you debugged a production outage.",
            ]),
            sec("Retrieval", 8, [
                "How would you design a FastAPI service for high traffic ?",
                "What trade-offs did you make in your RAG pipeline?",
            ]),
        ]
    )
    plan = finalize(d)
    flat = [q for s in plan.sections for q in s.seed_questions]
    assert len(flat) == 3
    assert len(set(flat)) == 3


def test_sections_are_capped_by_budget_and_ordered_by_weight():
    questions = [
        "Explain how Kubernetes schedules pods onto nodes.",
        "Describe how you would shard a Postgres table.",
        "Walk me through designing a rate limiter.",
        "How do you evaluate retrieval quality offline?",
        "What is your approach to code review?",
        "Describe a conflict with a teammate.",
    ]
    weights = [3, 9, 5, 8, 1, 7]
    d = draft([sec(f"Topic {i}", w, [questions[i]]) for i, w in enumerate(weights)])
    plan = finalize(d)
    assert len(plan.sections) == 4
    assert [s.weight for s in plan.sections] == [9, 8, 7, 5]
    assert sum(s.target_questions for s in plan.sections) == 9
    assert abs(sum(s.minutes for s in plan.sections) - 28.0) <= 0.3


def test_duplicate_competency_names_are_merged():
    d = draft(
        [
            sec("FastAPI design", 9, ["How do you structure routers in a large FastAPI app?"]),
            sec("fastapi   design", 8, ["What is your approach to dependency injection?"]),
            sec("Postgres tuning", 7, ["How do you find and fix a slow query?"]),
        ]
    )
    plan = finalize(d)
    assert [s.competency for s in plan.sections] == ["FastAPI design", "Postgres tuning"]


def test_fewer_than_two_usable_sections_is_invalid():
    same = ["Explain how indexes work in Postgres."]
    with pytest.raises(PlanInvalid):
        finalize(draft([sec("Indexes", 9, same), sec("Storage", 8, same)]))


def test_blank_opening_question_falls_back_to_a_default():
    d = draft(
        [
            sec("API design", 9, ["How do you structure routers in a large FastAPI app?"]),
            sec("Databases", 8, ["How do you find and fix a slow query?"]),
        ],
        opening="   ",
    )
    assert "ML Engineer" in finalize(d).opening_question


def test_canned_plan_validates_and_finalizes():
    plan = finalize(PlanDraft.model_validate(CANNED_PLAN))
    assert len(plan.sections) == 4
    assert sum(s.target_questions for s in plan.sections) == 9


def test_weight_is_clamped_into_range():
    base = {"competency": "Topic", "category": "behavioral", "seed_questions": ["One question?"]}
    assert PlanSectionDraft.model_validate({**base, "weight": 15}).weight == 10
    assert PlanSectionDraft.model_validate({**base, "weight": 0}).weight == 1