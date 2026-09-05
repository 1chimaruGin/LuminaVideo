"""Source language, and a transcript the creator supplied.

Two columns on `projects`:

  - `source_language` — the language of what went in, where it differs from what comes out.
    Null for every project that is not a translation, which is why it is nullable rather than
    backfilled: "no answer" and "the same as the target" are the same thing here.
  - `transcript_asset_id` — an SRT, WebVTT or plain script the creator already had. When it is
    set nothing is transcribed.

Autogenerate also proposed re-creating `fk_projects_current_plan`, which already exists — it
cannot see through `use_alter` — and emitted unnamed `drop_constraint` calls that fail at
runtime. Both are dropped here; the FK is named so the downgrade can actually find it.

Revision ID: 5d422a3ab10c
Revises: 5f1c9a2d7e40
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "5d422a3ab10c"
down_revision: str | None = "5f1c9a2d7e40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("source_language", sa.String(length=16), nullable=True))
    op.add_column("projects", sa.Column("transcript_asset_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_projects_transcript_asset", "projects", "assets", ["transcript_asset_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_projects_transcript_asset", "projects", type_="foreignkey")
    op.drop_column("projects", "transcript_asset_id")
    op.drop_column("projects", "source_language")
