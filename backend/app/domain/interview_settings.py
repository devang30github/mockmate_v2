from typing import Literal

from pydantic import BaseModel, Field, field_validator


class InterviewSettings(BaseModel):
    role_title: str = Field(min_length=2, max_length=120)
    duration_minutes: Literal[15, 30, 45, 60]
    difficulty: Literal["easy", "medium", "hard"]
    interview_type: Literal["technical", "behavioral", "mixed"]
    language: Literal["en"] = "en"  # kept as a field so more languages can be added later

    @field_validator("role_title")
    @classmethod
    def _strip_role(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Role title is too short")
        return v