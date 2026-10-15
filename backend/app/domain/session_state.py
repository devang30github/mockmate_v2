from enum import Enum


class State(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    ASKING = "ASKING"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    PROCESSING = "PROCESSING"
    PAUSED = "PAUSED"
    ERROR = "ERROR"
    FINISHING = "FINISHING"
    REPORT_PENDING = "REPORT_PENDING"
    COMPLETED = "COMPLETED"


class Event(str, Enum):
    PLAN_READY = "PLAN_READY"
    START = "START"
    TTS_DONE = "TTS_DONE"  # the browser reports the question audio finished playing
    SILENCE_REPROMPT = "SILENCE_REPROMPT"  # no speech for too long: ask again
    UTTERANCE_END = "UTTERANCE_END"
    STT_OK = "STT_OK"
    STT_EMPTY = "STT_EMPTY"  # nothing intelligible was heard
    STT_FAILED = "STT_FAILED"
    QUESTION_READY = "QUESTION_READY"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    FAIL = "FAIL"
    RETRY_ASKING = "RETRY_ASKING"
    RETRY_PROCESSING = "RETRY_PROCESSING"
    FINISH = "FINISH"  # candidate ended the interview, time ran out, or the plan is complete
    EVALS_DONE = "EVALS_DONE"
    REPORT_SAVED = "REPORT_SAVED"


S, E = State, Event

TRANSITIONS: dict[tuple[State, Event], State] = {
    (S.DRAFT, E.PLAN_READY): S.READY,
    (S.READY, E.START): S.ASKING,
    # one question/answer turn
    (S.ASKING, E.TTS_DONE): S.LISTENING,
    (S.LISTENING, E.SILENCE_REPROMPT): S.ASKING,
    (S.LISTENING, E.UTTERANCE_END): S.TRANSCRIBING,
    (S.TRANSCRIBING, E.STT_OK): S.PROCESSING,
    (S.TRANSCRIBING, E.STT_EMPTY): S.LISTENING,
    (S.TRANSCRIBING, E.STT_FAILED): S.LISTENING,
    (S.PROCESSING, E.QUESTION_READY): S.ASKING,
    # pause is only allowed while a question plays or while listening
    (S.ASKING, E.PAUSE): S.PAUSED,
    (S.LISTENING, E.PAUSE): S.PAUSED,
    (S.PAUSED, E.RESUME): S.ASKING,  # the current question is replayed
    # unrecoverable failures
    (S.ASKING, E.FAIL): S.ERROR,
    (S.LISTENING, E.FAIL): S.ERROR,
    (S.TRANSCRIBING, E.FAIL): S.ERROR,
    (S.PROCESSING, E.FAIL): S.ERROR,
    (S.ERROR, E.RETRY_ASKING): S.ASKING,
    (S.ERROR, E.RETRY_PROCESSING): S.PROCESSING,
    # finishing and the mandatory report
    (S.ASKING, E.FINISH): S.FINISHING,
    (S.LISTENING, E.FINISH): S.FINISHING,
    (S.TRANSCRIBING, E.FINISH): S.FINISHING,
    (S.PROCESSING, E.FINISH): S.FINISHING,
    (S.PAUSED, E.FINISH): S.FINISHING,
    (S.ERROR, E.FINISH): S.FINISHING,
    (S.FINISHING, E.EVALS_DONE): S.REPORT_PENDING,
    (S.REPORT_PENDING, E.REPORT_SAVED): S.COMPLETED,
}

ACTIVE_STATES = frozenset({S.ASKING, S.LISTENING, S.TRANSCRIBING, S.PROCESSING})
TERMINAL_STATES = frozenset({S.COMPLETED})


class InvalidTransition(Exception):
    def __init__(self, state: str, event: str):
        super().__init__(f"event {event} is not allowed in state {state}")
        self.state = state
        self.event = event


def transition(state: State | str, event: Event) -> State:
    try:
        current = State(state)
    except ValueError:
        raise InvalidTransition(str(state), event.value) from None
    try:
        return TRANSITIONS[(current, event)]
    except KeyError:
        raise InvalidTransition(current.value, event.value) from None


def allowed_events(state: State | str) -> frozenset[Event]:
    current = State(state)
    return frozenset(ev for (st, ev) in TRANSITIONS if st == current)


def accepts_audio(state: State | str) -> bool:
    """Microphone audio is only accepted while the interviewer is waiting for an answer."""
    return State(state) == State.LISTENING