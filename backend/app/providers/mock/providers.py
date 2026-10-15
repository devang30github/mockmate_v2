import asyncio
import hashlib
import io
import json
import math
import random
import re
import struct
import wave
from collections.abc import AsyncIterator

from app.providers.base import LLMMessage, LLMResult, STTResult, TTSResult

MOCK_LLM_TEXT = (
    "Thanks for that answer. Can you walk me through a specific project "
    "where you applied it? What trade-offs did you face?"
)
CANNED_ANALYSIS = {
    "profile": {
        "summary": "Mock candidate summary.",
        "experience_level": "mid",
        "years_experience": 4,
        "skills": ["Python", "FastAPI"],
        "technologies": ["PostgreSQL", "Docker"],
        "roles": [
            {
                "title": "Software Engineer",
                "organization": "Mock Corp",
                "period": "2021-2025",
                "highlights": ["Built a FastAPI service"],
            }
        ],
        "projects": [
            {
                "name": "Mock RAG pipeline",
                "summary": "Hybrid search over support documents.",
                "technologies": ["pgvector"],
                "outcomes": [],
            }
        ],
        "education": ["B.Tech Computer Science"],
    },
    "job": {
        "role_title": "Machine Learning Engineer",
        "summary": "Build retrieval-augmented generation systems.",
        "required_skills": ["Python", "FastAPI", "PostgreSQL"],
        "preferred_skills": ["Kubernetes"],
        "responsibilities": ["Build RAG systems"],
        "competencies": ["System design", "LLM evaluation"],
    },
    "alignment": {
        "strengths": ["Python and FastAPI are evidenced"],
        "gaps": ["No evidence of LLM evaluation work"],
    },
    "probe_claims": [
        {
            "claim": "Handled 2M requests per day",
            "reason": "No detail on the scaling approach",
            "source_section": "Experience",
        }
    ],
    "interview_topics": ["FastAPI service design", "RAG retrieval quality", "LLM evaluation"],
}

CANNED_PLAN = {
    "opening_question": (
        "Thanks for joining. To start, could you give me a short overview of your "
        "background and what draws you to this role?"
    ),
    "sections": [
        {
            "competency": "FastAPI service design",
            "category": "technical_depth",
            "rationale": "Core requirement of the role and well evidenced on the resume.",
            "weight": 9,
            "expected_depth": "intermediate",
            "seed_questions": [
                "How would you structure a FastAPI service that has to stay responsive under heavy load?",
                "Which parts of your Acme service were hardest to keep fast, and why?",
            ],
            "source_claims": [],
            "follow_up_strategy": "Ask about trade-offs and what they would change.",
        },
        {
            "competency": "RAG retrieval quality",
            "category": "technical_depth",
            "rationale": "The role centres on retrieval-augmented generation.",
            "weight": 8,
            "expected_depth": "advanced",
            "seed_questions": [
                "Walk me through how your hybrid search combined vector and keyword results.",
                "How did you decide whether retrieval was actually good enough?",
            ],
            "source_claims": [],
            "follow_up_strategy": "Probe how quality was measured.",
        },
        {
            "competency": "Scaling claim: 2M requests per day",
            "category": "resume_deep_dive",
            "rationale": "Ambitious claim with no detail on the approach.",
            "weight": 7,
            "expected_depth": "intermediate",
            "seed_questions": [
                "You mentioned handling two million requests a day. What was the first bottleneck you hit?",
                "What would you change about that architecture today?",
            ],
            "source_claims": ["Handled 2M requests per day"],
            "follow_up_strategy": "Ask for concrete numbers and the candidate's own contribution.",
        },
        {
            "competency": "LLM evaluation",
            "category": "problem_solving",
            "rationale": "Listed as a gap: no evidence of evaluation work.",
            "weight": 6,
            "expected_depth": "intermediate",
            "seed_questions": [
                "How would you measure whether an LLM feature is getting better or worse over time?",
                "Describe a failure case you found and how you fixed it.",
            ],
            "source_claims": [],
            "follow_up_strategy": "Separate not-demonstrated from incorrect.",
        },
        {
            "competency": "Collaboration and ownership",
            "category": "behavioral",
            "rationale": "Standard behavioural coverage.",
            "weight": 3,
            "expected_depth": "basic",
            "seed_questions": [
                "Tell me about a time you disagreed with a teammate on a technical decision.",
                "Describe a project you owned end to end and what you learned from it.",
            ],
            "source_claims": [],
            "follow_up_strategy": "Ask for the situation, the action, and the result.",
        },
    ],
    "follow_up_policy": (
        "Ask a follow-up when an answer is vague, unsupported, or skips the trade-offs. "
        "Move on after two follow-ups."
    ),
}

class MockLLM:
    model = "mock-llm"

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        if json_mode:
            system = " ".join(m.content for m in messages if m.role == "system")
            if "TASK:analyze_resume_jd" in system:
                text = json.dumps(CANNED_ANALYSIS)
            elif "TASK:plan_interview" in system:
                text = json.dumps(CANNED_PLAN)
            else:
                last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
                text = json.dumps({"mock": True, "echo": last_user[:40]})
        else:
            text = MOCK_LLM_TEXT
        return LLMResult(text=text, model=self.model, prompt_tokens=0, completion_tokens=0)

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        for chunk in re.findall(r"\S+\s*", MOCK_LLM_TEXT):
            await asyncio.sleep(0)
            yield chunk


class MockSTT:
    async def transcribe(
        self, pcm16: bytes, sample_rate: int = 16000, hints: list[str] | None = None
    ) -> STTResult:
        duration = len(pcm16) / (2 * sample_rate)
        return STTResult(
            text=f"mock transcript of {duration:.1f} seconds of audio",
            duration_s=duration,
            confidence=1.0,
        )


class MockTTS:
    """Returns a short, valid, playable WAV (a quiet 440 Hz tone)."""

    async def synthesize(self, text: str, *, voice: str | None = None) -> TTSResult:
        rate = 22050
        seconds = min(max(len(text) * 0.04, 0.3), 5.0)
        n = int(rate * seconds)
        frames = b"".join(
            struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(n)
        )
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(frames)
        return TTSResult(audio=buf.getvalue(), mime="audio/wav", chars=len(text))


class MockEmbeddings:
    dim = 384

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            seed = int(hashlib.sha256(t.encode()).hexdigest(), 16)
            rng = random.Random(seed)
            vec = [rng.uniform(-1, 1) for _ in range(self.dim)]
            norm = math.sqrt(sum(v * v for v in vec))
            out.append([v / norm for v in vec])
        return out