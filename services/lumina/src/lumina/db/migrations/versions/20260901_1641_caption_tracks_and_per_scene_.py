"""Caption tracks, and the same line in other languages.

Two columns:

  - `projects.caption_languages` — which tracks are burnt in, in the order they stack.
    Empty means "just the project's language", which is every ordinary project.
  - `scenes.translations` — the same cue in other languages, keyed by BCP-47 tag.

Both default rather than backfill: an existing project has one language and no translations,
which is exactly what the defaults say.

Autogenerate also proposed re-creating `fk_projects_current_plan`, which already exists — it
cannot see through `use_alter`. Dropped.

Revision ID: 8a3f1c05e7b2
Revises: 5d422a3ab10c
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8a3f1c05e7b2"
down_revision: str | None = "5d422a3ab10c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "caption_languages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "scenes",
        sa.Column(
            "translations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("scenes", "translations")
    op.drop_column("projects", "caption_languages")
