"""interview sessions

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interview_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resume_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("jd_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(length=20), server_default=sa.text("'DRAFT'"), nullable=False),
        sa.Column("state_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_interview_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_interview_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resume_document_id"],
            ["documents.id"],
            name="fk_interview_sessions_resume_document_id_documents",
        ),
        sa.ForeignKeyConstraint(
            ["jd_document_id"],
            ["documents.id"],
            name="fk_interview_sessions_jd_document_id_documents",
        ),
    )
    op.create_index("ix_interview_sessions_user_id", "interview_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_table("interview_sessions")