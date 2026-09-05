"""SQLAlchemy models.

One deliberate split to note: a Plan's *authored* content lives in `plans.content` as JSONB,
but each scene's *mutable* state lives in its own row in `scenes`. Keeping scenes inside the
plan JSONB would make every per-scene update a read-modify-write of the whole document, and
two scenes finishing at the same moment would lose one of the two writes. Since the entire
product promise is "regenerating one card does not disturb the others" (invariant 5), scenes
get their own rows and their own row locks.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lumina.db.base import Base


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)  # null for OAuth-only accounts
    display_name: Mapped[str | None] = mapped_column(String(120))
    locale: Mapped[str] = mapped_column(String(16), default="en", nullable=False)

    channels: Mapped[list[Channel]] = relationship(back_populates="user")


class Channel(Base, TimestampMixin):
    """The identity kit. Inherited by every new project — this is what makes episode 2 look
    like episode 1 without the user configuring anything."""

    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="en", nullable=False)

    voice_profile_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"))
    logo_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"))
    #: palette, fonts, caption_style, intro/outro — one document, versioned with the channel.
    identity: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    user: Mapped[User] = relationship(back_populates="channels")


class OAuthIdentity(Base, TimestampMixin):
    """A provider account linked to a Lumina user.

    Keyed on (provider, subject) rather than on email, because the subject is the only thing a
    provider promises is stable. Someone who changes their Google address would otherwise come
    back as a stranger and get a second account — with a second welcome grant and none of
    their projects.
    """

    __tablename__ = "oauth_identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_oauth_provider_subject"),)

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    #: The provider's own id for this person. Opaque, and never an email.
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    #: What the address was when it was linked. Kept for support, never for matching.
    email: Mapped[str | None] = mapped_column(String(320))


class Asset(Base, TimestampMixin):
    """Content-addressed and immutable (invariant 3). `sha256` is the dedup key: the same
    prompt with the same refs and seed produces the same bytes and reuses this row instead
    of paying a provider again."""

    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("sha256", "kind", name="uq_assets_sha256_kind"),)

    id: Mapped[uuid.UUID] = _pk()
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    #: Provenance: which provider made it, and any watermark that must survive composition.
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    recipe: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The language of what comes *out* — captions, narration, the pack that renders them.
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The language of what went *in*, where that differs. Null means "the same as `language`",
    #: which is every project that is not a translation. Kept apart from `language` because
    #: they drive different halves of the pipeline: the source picks the speech engine, the
    #: target picks the typography, the reading speed and the font.
    source_language: Mapped[str | None] = mapped_column(String(16))
    #: Which voice a dub is spoken in. None until the creator picks one — see the migration.
    voice_id: Mapped[str | None] = mapped_column(String(64))
    #: The file this project was made from, for the lanes that start from one. Null for the
    #: lanes that start from an idea — those have no footage, they generate every frame.
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"))
    #: Timed text the creator supplied — an SRT, a WebVTT track, or a plain script. When this
    #: is set nothing is transcribed: reading it is exact, free, and works with no speech
    #: engine installed. See `execution.subtitles`.
    transcript_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"))
    #: Which caption tracks are burnt in, in the order they are stacked — primary first.
    #:
    #: Empty means "just `language`", which is every ordinary project. More than one is a
    #: bilingual subtitle: the same cue, at the same moment, in two or three languages at
    #: once. That is the normal way anime, lectures and language-learning clips are captioned,
    #: and it is one video rather than three because the timings are shared — which is the
    #: whole reason the translations live on the scene rather than in a plan of their own.
    caption_languages: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    #: Points at the plan version the storyboard is currently showing.
    current_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plans.id", use_alter=True, name="fk_projects_current_plan")
    )


class Plan(Base, TimestampMixin):
    """Versioned and user-editable, never hidden reasoning (invariant 5). Editing a plan
    creates a new version; old versions stay readable so a user can revert."""

    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_plans_project_version"),)

    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The authored plan: title, summary, aspects. Validated by domain.plan.Plan on read.
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Ordered so the API never has to sort, and never returns scenes in storage order.
    scenes: Mapped[list[Scene]] = relationship(
        back_populates="plan", order_by="Scene.index", cascade="all, delete-orphan"
    )


class Scene(Base, TimestampMixin):
    """Own row, own lock. See the module docstring."""

    __tablename__ = "scenes"
    __table_args__ = (
        UniqueConstraint("plan_id", "index", name="uq_scenes_plan_index"),
        CheckConstraint("duration_ms > 0", name="ck_scenes_duration_positive"),
    )

    id: Mapped[uuid.UUID] = _pk()
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)

    plan: Mapped[Plan] = relationship(back_populates="scenes")

    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    script_line: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    #: The same line in other languages, keyed by BCP-47 tag: `{"my": "...", "ja": "..."}`.
    #:
    #: On the scene rather than in a parallel plan because a translation is the same cue said
    #: differently — it appears at the same moment, for the same length, and re-timing it
    #: would put it out of sync with the speech it translates. One row, one moment, N texts.
    translations: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Where in the project's source video this scene is, for the lanes that keep footage.
    #: Null means "there is no source" — the scene's picture is generated instead. Kept per
    #: scene rather than derived from the running duration because a clip's moments are not
    #: contiguous: two moments forty minutes apart are scenes one and two.
    source_start_ms: Mapped[int | None] = mapped_column(Integer)

    tier: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(Text)

    # Separate columns on purpose: a final NEVER overwrites a preview, so the user can revert.
    preview_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"))
    final_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"))
    vo_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"))
    ref_asset_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)


class Job(Base, TimestampMixin):
    """One execution of one stage. Also the queue table — see lumina.queue."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claimable", "pool", "status", "run_after"),
        Index("ix_jobs_lease", "status", "claimed_until"),
    )

    id: Mapped[uuid.UUID] = _pk()
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    pool: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")

    #: Enqueuing identical work twice is a no-op. This constraint is invariant 3.
    idempotency_key: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    scene_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )

    # COGS telemetry. The monthly reconciliation against provider invoices is a query on
    # these three columns; estimator drift is a chart, not a guess.
    provider: Mapped[str | None] = mapped_column(String(48))
    provider_ref: Mapped[str | None] = mapped_column(String(200))
    estimated_cents: Mapped[int | None] = mapped_column(Integer)
    actual_cents: Mapped[int | None] = mapped_column(Integer)


class LedgerEntry(Base):
    """Double-entry. The balance is derived by folding these, never stored as a column."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_ledger_amount_non_negative"),
        CheckConstraint(
            "(kind = 'grant') OR (reserve_entry_id IS NOT NULL) OR (kind = 'reserve')",
            name="ck_ledger_discharge_references_reserve",
        ),
        # A reserve is discharged exactly once: one COMMIT or one REFUND, never both.
        Index(
            "uq_ledger_single_discharge",
            "reserve_entry_id",
            unique=True,
            postgresql_where=text("kind IN ('commit', 'refund')"),
        ),
        Index("ix_ledger_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    reserve_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ledger_entries.id"))
    reason: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Render(Base, TimestampMixin):
    """One delivered file. A single storyboard produces 9:16, 1:1 and 16:9 — three rows."""

    __tablename__ = "renders"

    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"), nullable=False)
    aspect: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"))
