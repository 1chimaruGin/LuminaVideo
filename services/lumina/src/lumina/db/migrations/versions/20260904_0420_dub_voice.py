"""Which voice a dub is spoken in.

One nullable column on `projects`. Nullable rather than defaulted to a voice name because
"not chosen yet" and "chosen, and they picked the first one" are different states: the Brief
screen asks for a voice before the render, and a default would make an unanswered question
look answered.

The value is a vendor voice id (`Kore`, `Sulafat`). It is stored rather than resolved at
render time because a creator who dubbed episode one in a voice expects episode two to be
offered the same one, and because re-rendering a project must not silently change how it
sounds.

Revision ID: c4e91d7a2b60
Revises: 8a3f1c05e7b2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c4e91d7a2b60"
down_revision: str | None = "8a3f1c05e7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("voice_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "voice_id")
