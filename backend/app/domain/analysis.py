from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field


def _clip(n: int):
    """Truncate over-long lists instead of failing the whole response."""

    def inner(v):
        if v is None:
            return []
        if isinstance(v, list):
            return v[:n]
        return v

    return inner


StrList = Annotated[list[str], BeforeValidator(_clip(30))]


class RoleEntry(BaseModel):
    title: str
    organization: str = ""
    period: str | None = None
    highlights: StrList = Field(default_factory=list)


class Project(BaseModel):
    name: str
    summary: str = ""
    technologies: StrList = Field(default_factory=list)
    outcomes: StrList = Field(default_factory=list)


class ResumeProfile(BaseModel):
    summary: str
    experience_level: Literal["entry", "mid", "senior", "unclear"] = "unclear"
    years_experience: float | None = None
    skills: StrList = Field(default_factory=list)
    technologies: StrList = Field(default_factory=list)
    roles: Annotated[list[RoleEntry], BeforeValidator(_clip(12))] = Field(default_factory=list)
    projects: Annotated[list[Project], BeforeValidator(_clip(12))] = Field(default_factory=list)
    education: StrList = Field(default_factory=list)


class JobRequirements(BaseModel):
    role_title: str
    summary: str = ""
    required_skills: StrList = Field(default_factory=list)
    preferred_skills: StrList = Field(default_factory=list)
    responsibilities: StrList = Field(default_factory=list)
    competencies: StrList = Field(default_factory=list)


class Alignment(BaseModel):
    strengths: StrList = Field(default_factory=list)
    gaps: StrList = Field(default_factory=list)


class ProbeClaim(BaseModel):
    claim: str
    reason: str
    source_section: str | None = None


class Analysis(BaseModel):
    profile: ResumeProfile
    job: JobRequirements
    alignment: Alignment = Field(default_factory=Alignment)
    probe_claims: Annotated[list[ProbeClaim], BeforeValidator(_clip(12))] = Field(
        default_factory=list
    )
    interview_topics: StrList = Field(default_factory=list)