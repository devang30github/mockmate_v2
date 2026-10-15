from app.domain.stt_hints import PROMPT_PREFIX, build_initial_prompt, extract_terms

ANALYSIS = {
    "profile": {
        "technologies": ["PostgreSQL", "Docker"],
        "skills": ["Python", "docker", "FastAPI"],
    },
    "job": {"required_skills": ["Kubernetes", "Python"], "preferred_skills": ["Terraform"]},
}


def test_technologies_come_first_and_duplicates_are_removed_ignoring_case():
    assert extract_terms(ANALYSIS) == [
        "PostgreSQL", "Docker", "Python", "FastAPI", "Kubernetes", "Terraform",
    ]


def test_limit_is_respected():
    big = {"profile": {"skills": [f"Skill{i}" for i in range(100)]}}
    assert len(extract_terms(big, limit=10)) == 10


def test_blank_overlong_and_non_string_values_are_skipped():
    bad = {"profile": {"skills": ["", "   ", "x" * 41, 5, None, "ok"]}}
    assert extract_terms(bad) == ["ok"]


def test_control_characters_are_removed():
    assert extract_terms({"profile": {"skills": ["Fast\x00API\nX"]}}) == ["Fast API X"]


def test_missing_or_malformed_sections_are_tolerated():
    assert extract_terms({}) == []
    assert extract_terms({"profile": None, "job": "oops"}) == []


def test_prompt_is_none_without_usable_terms():
    assert build_initial_prompt([]) is None
    assert build_initial_prompt(["", "  "]) is None


def test_prompt_lists_terms_in_order():
    assert build_initial_prompt(["A1", "B2"]) == "Technical job interview. Terms: A1, B2."


def test_prompt_respects_the_character_limit():
    terms = [f"Term{i:02d}" for i in range(100)]
    prompt = build_initial_prompt(terms, max_chars=120)
    assert len(prompt) <= 120
    assert prompt.startswith(PROMPT_PREFIX) and prompt.endswith(".")
    assert "Term00" in prompt and "Term99" not in prompt