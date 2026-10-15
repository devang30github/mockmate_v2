import itertools

import pytest

from app.domain.session_state import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    Event,
    InvalidTransition,
    State,
    accepts_audio,
    allowed_events,
    transition,
)

S, E = State, Event

HAPPY_PATH = [
    (S.READY, E.START, S.ASKING),
    (S.ASKING, E.TTS_DONE, S.LISTENING),
    (S.LISTENING, E.UTTERANCE_END, S.TRANSCRIBING),
    (S.TRANSCRIBING, E.STT_OK, S.PROCESSING),
    (S.PROCESSING, E.QUESTION_READY, S.ASKING),
]


@pytest.mark.parametrize("state,event,expected", HAPPY_PATH)
def test_one_turn_happy_path(state, event, expected):
    assert transition(state, event) == expected


def test_full_interview_walks_to_completed():
    state = S.DRAFT
    for event in [
        E.PLAN_READY, E.START, E.TTS_DONE, E.UTTERANCE_END, E.STT_OK, E.QUESTION_READY,
        E.TTS_DONE, E.UTTERANCE_END, E.STT_OK, E.FINISH, E.EVALS_DONE, E.REPORT_SAVED,
    ]:
        state = transition(state, event)
    assert state == S.COMPLETED


@pytest.mark.parametrize(
    "pair", [p for p in itertools.product(State, Event) if p not in TRANSITIONS]
)
def test_every_undefined_pair_is_rejected(pair):
    state, event = pair
    with pytest.raises(InvalidTransition):
        transition(state, event)


def test_unknown_state_value_is_rejected():
    with pytest.raises(InvalidTransition):
        transition("BOGUS", E.START)


def test_accepts_strings_from_the_database():
    assert transition("READY", E.START) == S.ASKING


def test_completed_is_terminal():
    assert TERMINAL_STATES == {S.COMPLETED}
    assert allowed_events(S.COMPLETED) == frozenset()


def test_pause_only_while_asking_or_listening():
    can_pause = {st for st in State if (st, E.PAUSE) in TRANSITIONS}
    assert can_pause == {S.ASKING, S.LISTENING}


def test_resume_replays_the_question():
    assert transition(S.PAUSED, E.RESUME) == S.ASKING


def test_every_active_state_can_finish_and_fail():
    for st in ACTIVE_STATES:
        assert transition(st, E.FINISH) == S.FINISHING
        assert transition(st, E.FAIL) == S.ERROR


def test_error_can_retry_or_finish():
    assert transition(S.ERROR, E.RETRY_ASKING) == S.ASKING
    assert transition(S.ERROR, E.RETRY_PROCESSING) == S.PROCESSING
    assert transition(S.ERROR, E.FINISH) == S.FINISHING


def test_empty_or_failed_transcription_goes_back_to_listening():
    assert transition(S.TRANSCRIBING, E.STT_EMPTY) == S.LISTENING
    assert transition(S.TRANSCRIBING, E.STT_FAILED) == S.LISTENING


def test_report_cannot_complete_before_evaluations_are_done():
    assert (S.FINISHING, E.REPORT_SAVED) not in TRANSITIONS
    assert transition(S.FINISHING, E.EVALS_DONE) == S.REPORT_PENDING
    assert transition(S.REPORT_PENDING, E.REPORT_SAVED) == S.COMPLETED


def test_audio_is_only_accepted_while_listening():
    assert [st for st in State if accepts_audio(st)] == [S.LISTENING]


def test_every_state_is_reachable_from_draft():
    seen, frontier = {S.DRAFT}, [S.DRAFT]
    while frontier:
        current = frontier.pop()
        for (src, _), dst in TRANSITIONS.items():
            if src == current and dst not in seen:
                seen.add(dst)
                frontier.append(dst)
    assert seen == set(State)