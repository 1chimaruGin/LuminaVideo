"""Retitle projects from the plan they already have.

Until now a project's title was the first 80 characters of its brief. For the two lanes whose
whole input is a file the brief *is* the filename, so those projects were listed as
`ch11_20260825_1600-1605.mp4`; for the rest it was a sentence fragment cut mid-word. The
planner has always written a real short title — it was just never adopted.

`stages.plan` now copies it across at planning time. This carries the same rule backwards over
rows that were planned before that, and touches nothing else: a project with no plan, or a
plan whose content carries no usable title, keeps what it has. There is no rename feature, so
no title here was chosen by a person.

Revision ID: 5f1c9a2d7e40
Revises: 370ba758488a
"""

from __future__ import annotations

from alembic import op

revision = "5f1c9a2d7e40"
down_revision = "370ba758488a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE projects AS p
           SET title = LEFT(BTRIM(pl.content ->> 'title'), 80)
          FROM plans AS pl
         WHERE pl.id = p.current_plan_id
           AND BTRIM(COALESCE(pl.content ->> 'title', '')) <> ''
           AND p.title IS DISTINCT FROM LEFT(BTRIM(pl.content ->> 'title'), 80)
        """
    )


def downgrade() -> None:
    """The briefs these titles replaced are not stored, so there is nothing to restore."""
