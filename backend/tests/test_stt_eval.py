from app.services.stt_eval import (
    edit_distance,
    normalize,
    term_found,
    terms_in,
    wer,
    wer_counts,
)


def test_normalize_strips_punctuation_but_keeps_plus_and_hash():
    assert normalize("Hello, World! C++ and C#") == "hello world c++ and c#"


def test_identical_text_has_zero_wer():
    assert wer("the cat sat", "The cat sat.") == 0


def test_insertion_deletion_and_substitution_are_counted():
    assert wer_counts("the cat sat", "the cat sat down") == (1, 3)
    assert wer_counts("the cat sat", "the sat") == (1, 3)
    assert wer_counts("use fast api", "use fastapi") == (2, 3)


def test_empty_reference():
    assert wer("", "") == 0
    assert wer("", "hello") == 1.0


def test_edit_distance_against_empty():
    assert edit_distance(["a", "b"], []) == 2


def test_strict_term_match_needs_whole_words():
    assert term_found("pgvector", "I used pgvector for search")
    assert not term_found("pgvector", "I used pg vector for search")


def test_lenient_match_ignores_spaces():
    assert not term_found("FastAPI", "we use fast API daily")
    assert term_found("FastAPI", "we use fast API daily", lenient=True)


def test_punctuated_and_symbol_terms():
    assert term_found("CI/CD", "we run ci cd pipelines")
    assert term_found("C++", "I write c++ daily")
    assert not term_found("C++", "I write c daily")


def test_short_terms_stay_strict_even_when_lenient():
    assert not term_found("Go", "i am going home", lenient=True)


def test_terms_in_returns_only_terms_present_in_the_reference():
    assert terms_in("we used Docker and Redis", ["Docker", "Kafka", "Redis"]) == ["Docker", "Redis"]