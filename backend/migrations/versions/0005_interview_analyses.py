"""interview analyses

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interview_analyses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("interview_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=30), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_interview_analyses"),
        sa.ForeignKeyConstraint(
            ["interview_id"],
            ["interview_sessions.id"],
            name="fk_interview_analyses_interview_id_interview_sessions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("interview_id", name="uq_interview_analyses_interview_id"),
    )


def downgrade() -> None:
    op.drop_table("interview_analyses")