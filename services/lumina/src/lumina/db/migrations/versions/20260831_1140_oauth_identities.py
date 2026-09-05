"""oauth identities

Links a provider account (Google, GitHub, Apple) to a Lumina user.

Keyed on (provider, subject) rather than on email: the subject is the only identifier a
provider promises is stable. Matching on email means someone who changes their Google address
comes back as a stranger — a second account, a second welcome grant, and none of their work.

Revision ID: 370ba758488a
Revises: 9c7e23e55c7f
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "370ba758488a"
down_revision: str | None = "9c7e23e55c7f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One Lumina account per provider account. Without this a race between two callbacks
        # links the same Google account to two users, and each sees half the work.
        sa.UniqueConstraint("provider", "subject", name="uq_oauth_provider_subject"),
    )
    op.create_index(
        op.f("ix_oauth_identities_user_id"), "oauth_identities", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_oauth_identities_user_id"), table_name="oauth_identities")
    op.drop_table("oauth_identities")
