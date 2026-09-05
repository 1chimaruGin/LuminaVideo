"""source footage on projects and scenes

Two lanes keep the creator's own footage rather than generating frames — Subtitles only and
Clip my long video. Before this, nothing recorded *which* file a project came from or where in
it a scene sat, so `compose` had no way to cut the source and refused to render a plan whose
scenes had no generated picture.

`scenes.source_start_ms` is per scene rather than derived from the running duration because a
clip's moments are not contiguous: two moments forty minutes apart are scenes one and two.

Revision ID: 9c7e23e55c7f
Revises: 369eb92b2137
Create Date: 2026-08-31 03:36:53.843478
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "9c7e23e55c7f"
down_revision: str | None = "369eb92b2137"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("source_asset_id", sa.UUID(), nullable=True))
    # Named explicitly. Autogenerate emitted `None`, which Postgres resolves to a name of its
    # own choosing and leaves the downgrade unable to drop it.
    op.create_foreign_key(
        "fk_projects_source_asset", "projects", "assets", ["source_asset_id"], ["id"]
    )
    op.add_column("scenes", sa.Column("source_start_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("scenes", "source_start_ms")
    op.drop_constraint("fk_projects_source_asset", "projects", type_="foreignkey")
    op.drop_column("projects", "source_asset_id")
