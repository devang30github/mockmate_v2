import logging
import re
from typing import TypeVar

from pydantic import BaseModel

from app.providers.base import LLMMessage, LLMProvider, LLMResult

log = logging.getLogger("mockmate.structured")
T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(Exception):
    pass


def extract_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


async def complete_structured(
    llm: LLMProvider,
    messages: list[LLMMessage],
    model_cls: type[T],
    *,
    max_tokens: int | None = None,
    repairs: int = 1,
) -> tuple[T, LLMResult]:
    msgs = list(messages)
    for attempt in range(repairs + 1):
        result = await llm.complete(msgs, json_mode=True, max_tokens=max_tokens, temperature=0.2)
        try:
            return model_cls.model_validate_json(extract_json(result.text)), result
        except ValueError as e:  # pydantic.ValidationError is a ValueError
            log.warning("structured output invalid attempt=%d", attempt + 1)
            msgs = msgs + [
                LLMMessage("assistant", result.text[:2000]),
                LLMMessage(
                    "user",
                    f"Your reply was invalid: {str(e)[:500]}\n"
                    "Reply again with only the corrected JSON object.",
                ),
            ]
    raise StructuredOutputError("model output failed validation after repair")