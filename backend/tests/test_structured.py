import json

import pytest

from app.domain.analysis import Analysis
from app.providers.base import LLMMessage, LLMResult
from app.providers.mock.providers import CANNED_ANALYSIS
from app.services.structured import StructuredOutputError, complete_structured

pytestmark = pytest.mark.anyio

VALID = json.dumps(CANNED_ANALYSIS)


class ScriptedLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        return LLMResult(text=self.outputs.pop(0), model="scripted")

    def stream(self, messages, **kwargs):
        raise NotImplementedError


MSGS = [LLMMessage("user", "x")]


async def test_valid_output_parses_in_one_call():
    llm = ScriptedLLM([VALID])
    parsed, _ = await complete_structured(llm, MSGS, Analysis)
    assert parsed.profile.skills == ["Python", "FastAPI"]
    assert llm.calls == 1


async def test_code_fenced_json_is_accepted():
    llm = ScriptedLLM([f"```json\n{VALID}\n```"])
    parsed, _ = await complete_structured(llm, MSGS, Analysis)
    assert parsed.job.role_title == "Machine Learning Engineer"


async def test_invalid_then_valid_uses_exactly_one_repair():
    llm = ScriptedLLM(["this is not json", VALID])
    parsed, _ = await complete_structured(llm, MSGS, Analysis)
    assert parsed.profile.summary
    assert llm.calls == 2


async def test_gives_up_after_bounded_repairs():
    llm = ScriptedLLM(["nope", "still nope"])
    with pytest.raises(StructuredOutputError):
        await complete_structured(llm, MSGS, Analysis)
    assert llm.calls == 2