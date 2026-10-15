"""interview plans

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interview_plans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("interview_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=30), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_interview_plans"),
        sa.ForeignKeyConstraint(
            ["interview_id"],
            ["interview_sessions.id"],
            name="fk_interview_plans_interview_id_interview_sessions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "interview_id", "version", name="uq_interview_plans_interview_id_version"
        ),
    )


def downgrade() -> None:
    op.drop_table("interview_plans")