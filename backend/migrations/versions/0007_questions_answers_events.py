"""interview questions, answers, transcript events

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "interview_questions",
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("interview_id", UUID, nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("competency", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_interview_questions"),
        sa.ForeignKeyConstraint(
            ["interview_id"],
            ["interview_sessions.id"],
            name="fk_interview_questions_interview_id_interview_sessions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "interview_id", "seq", name="uq_interview_questions_interview_id_seq"
        ),
    )

    op.create_table(
        "interview_answers",
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("interview_id", UUID, nullable=False),
        sa.Column("question_id", UUID, nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("utterance_id", UUID, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("audio_ms", sa.Integer(), nullable=False),
        sa.Column("stt_model", sa.String(length=100), nullable=False),
        sa.Column("stt_latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_interview_answers"),
        sa.ForeignKeyConstraint(
            ["interview_id"],
            ["interview_sessions.id"],
            name="fk_interview_answers_interview_id_interview_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["interview_questions.id"],
            name="fk_interview_answers_question_id_interview_questions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("interview_id", "seq", name="uq_interview_answers_interview_id_seq"),
        sa.UniqueConstraint("utterance_id", name="uq_interview_answers_utterance_id"),
    )

    op.create_table(
        "transcript_events",
        sa.Column("event_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("interview_id", UUID, nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_transcript_events"),
        sa.ForeignKeyConstraint(
            ["interview_id"],
            ["interview_sessions.id"],
            name="fk_transcript_events_interview_id_interview_sessions",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_transcript_events_interview_id", "transcript_events", ["interview_id"])


def downgrade() -> None:
    op.drop_table("transcript_events")
    op.drop_table("interview_answers")
    op.drop_table("interview_questions")