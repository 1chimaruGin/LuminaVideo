"""Wire types.

Separate from the domain and the ORM on purpose: these are the shapes the frontend's
generated client is built from, so a rename in the database must not silently become a
breaking API change.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SceneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    index: int
    prompt: str
    script_line: str
    caption: str | None
    #: The same line in the other burnt-in languages, keyed by BCP-47 tag. Empty for an
    #: ordinary single-language video.
    translations: dict[str, str] = Field(default_factory=dict)
    duration_ms: int
    #: Where this scene sits inside the project's source video, for the lanes that keep the
    #: creator's footage. Null when the picture is generated. The player seeks by it — a
    #: clip's moments are not contiguous, so the running total lands in the middle of neither.
    source_start_ms: int | None
    tier: str
    state: str
    error: str | None

    preview_asset_id: uuid.UUID | None
    final_asset_id: uuid.UUID | None
    vo_asset_id: uuid.UUID | None

    #: Which language *this* line is in.
    #:
    #: A project has one `source_language`, and for a file that changed language part way
    #: through that is only the commonest answer. A video of Japanese speech over an English
    #: song came back 35 English lines and 6 that were not — so a column headed "English" was
    #: wrong on six rows, and worse, those rows had been translated from the wrong language.
    #:
    #: Filled by the caller rather than computed here, because the answer depends on the other
    #: lines: kanji with no kana beside it is Chinese on its own and Japanese in a Japanese
    #: file, and a scene cannot see its siblings.
    spoken_language: str | None = None


class Moment(BaseModel):
    """One stretch of a long video that the ranker thought was worth posting.

    The score travels with it because the ranking is a judgement, not a measurement. A
    creator who disagrees needs to see that it was a guess rather than a verdict — which is
    also why nothing is silently discarded on the strength of it.
    """

    start_ms: int
    end_ms: int
    score: int
    line: str


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: Every later call is keyed by the project, not the plan, so creating one has to answer
    #: with it — otherwise the client has to re-list and guess which row it just made.
    project_id: uuid.UUID
    version: int
    title: str
    summary: str
    scenes: list[SceneOut]
    #: How long the finished video will be. Not derivable by summing the scenes: subtitling
    #: hands the source back whole, so its output is the length of the video that went in,
    #: while its scenes only cover the moments that have captions. The screen showed both
    #: numbers at once — "Subtitles for 201 seconds of video" above "7 scenes · 24s" — and
    #: neither explained the other.
    duration_ms: int = 0
    #: Which caption tracks are burnt in, in the order they stack. One entry is an ordinary
    #: video; more is a bilingual subtitle.
    caption_languages: list[str] = Field(default_factory=list)
    #: Whether the creator's footage comes back whole. Drives what the editor shows: a lane
    #: that keeps the video can play it under the captions; one that re-cuts it cannot, until
    #: it has rendered.
    keeps_whole_source: bool = False
    #: Which lane this plan belongs to.
    #:
    #: Sent with the plan rather than looked up in the project list, because every screen that
    #: renders a plan needs to know its lane and the list is a different request with a
    #: different lifetime. The editor used to read the lane out of that list and fall back to
    #: "explainer" until it arrived — so on a reload a Dub project drew the Subtitle editor,
    #: with Subtitle's wording and no voice controls, for as long as the list took to land.
    recipe: str = "explainer"
    #: The file this project was made from, for the lanes that start from one. Without it the
    #: player has nothing to show until a render exists — the creator uploads a video and then
    #: cannot watch the video they uploaded.
    source_asset_id: uuid.UUID | None = None
    #: Only the Clip lane has these; every other recipe writes its scenes rather than finding
    #: them, so there is no ranking to show.
    moments: list[Moment] = Field(default_factory=list)
    #: What the captions come out as.
    language: str = "en"
    #: What was actually spoken, when that differs. `None` means the project is not a
    #: translation — the scene's own `script_line` is already the language being written.
    #:
    #: The editor needs both to say anything useful about a line: a scene carries the source
    #: text and a map of translations, and without knowing which language the source *is*,
    #: "show me the original" has no answer.
    source_language: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    recipe: str
    language: str
    current_plan_id: uuid.UUID | None
    #: When it was started. What actually tells two episodes of a series apart — the titles
    #: are often near-identical, and the lane name is the same for every one of them.
    created_at: datetime
    #: The first scene's frame, if it has been drawn. A picture identifies a past project far
    #: faster than a truncated line of its brief does.
    poster_asset_id: uuid.UUID | None = None
    #: The footage this was made from, for the lanes that start from a file.
    #:
    #: Exposed so a creator can start a new job from a video they have already uploaded rather
    #: than sending the same bytes again. The same file is often wanted twice — subtitle it,
    #: then cut it — and the upload is the slowest step in the product.
    source_asset_id: uuid.UUID | None = None
    #: How long that footage runs, so the picker can say so without fetching each asset.
    source_duration_ms: int | None = None
    #: Which voice this dub is spoken in, so reopening the project shows the choice that was
    #: made rather than an unanswered picker.
    voice_id: str | None = None


class VoiceOut(BaseModel):
    """A voice a dub can be spoken in.

    `character` rather than a name or a gender because that is the only part a creator can
    act on — they are choosing how the video sounds, and "Sulafat" describes nothing. The
    sample endpoint is what actually answers the question; this is the label on the button.
    """

    id: str
    character: str
    #: True for the creator's own recorded voice rather than an engine voice.
    mine: bool = False
    #: Why this option cannot be used, if it cannot. None means it can.
    unavailable: str | None = None


class LanguageOut(BaseModel):
    """A language Lumina can actually render.

    Served rather than hardcoded in the client: a pack is what makes a language renderable —
    its font, its reading speed, its unit segmentation — so the list of packs *is* the list of
    languages, and adding one must not need a frontend release.
    """

    code: str
    #: What it calls itself, in its own script. A Burmese creator is looking for "မြန်မာ".
    name: str
    #: English name, for the creator who is picking a language they do not read.
    english: str
    #: Whether speech can be read *from* this language on this server. False when no engine is
    #: configured, which is when the creator needs to be told to attach a subtitle file.
    can_listen: bool
    #: Whether captions can be drawn in it — the face is present.
    can_render: bool
    #: Line spacing this script needs. Sent because the caption preview has to match the
    #: rasterizer: Burmese stacks marks above and below and needs 1.75 where English needs
    #: 1.4, and a preview using its own number shows a wrap the export does not produce.
    line_height: float = 1.4
    #: Characters per second a reader of this language sustains. Sent so the editor can tell,
    #: without a round trip, which cues are too fast to read — it varies by about 3x across
    #: languages, so a number baked into the client would be right for English and wrong for
    #: every script that is not English.
    cps: float = 17.0
    #: A language creators commonly shoot in, and one they commonly subtitle into. Ordering
    #: hints for the two pickers, not restrictions: every language is offered on both sides.
    #: They overlap on purpose — Japanese is filmed in and subtitled into.
    common_source: bool = False
    common_target: bool = False
    #: Whether a dub can be *spoken* in this language here. A third, separate answer from
    #: `can_listen` and `can_render`: a server can read Burmese captions onto a video and
    #: still have no engine able to say them out loud.
    can_speak: bool = False


class NewProject(BaseModel):
    brief: str = Field(min_length=1, max_length=20_000)
    recipe: str = "explainer"
    #: What comes out: the caption language, the narration language, the pack that renders it.
    language: str = "en"
    #: What went in, when it differs. Null means "the same" — every project that is not a
    #: translation. Used to pick the speech engine's language, never the typography.
    source_language: str | None = None
    #: Which voice a dub is spoken in. Null on every other lane, and on a dub that has not
    #: been asked yet — see the column comment in the migration.
    voice_id: str | None = None
    #: What the creator called it, when they said.
    #:
    #: Separate from `brief` because the two are different things on a lane that starts from a
    #: file: the brief is the content, the title is what to call it. They used to be one field,
    #: so a typed name became the brief, became the project's title, and was then overwritten
    #: by the plan's — which on a transcribed lane is its first caption. Naming a video did
    #: nothing at all.
    title: str | None = Field(default=None, max_length=80)
    target_ms: int = Field(default=60_000, ge=5_000, le=180_000)
    #: Set for the lanes that start from a file the user already has. The brief is then a
    #: title or an instruction rather than the content, and the source is what gets worked on.
    source_asset_id: uuid.UUID | None = None
    #: Timed text the creator already has — an SRT, a WebVTT track, or the plain script. When
    #: this is set nothing is transcribed: reading it is exact, free, and needs no speech
    #: engine. Listening is the fallback, not the default.
    transcript_asset_id: uuid.UUID | None = None


class SceneEdit(BaseModel):
    """Partial edit. Every field is optional so one change never overwrites the others."""

    prompt: str | None = None
    script_line: str | None = None
    caption: str | None = None
    duration_ms: int | None = Field(default=None, gt=0, le=60_000)
    #: Corrections to this line in other languages, merged rather than replaced.
    #:
    #: A machine translation is a draft. Nobody could fix one before this existed: the map was
    #: written by the translator and by nothing else, so a Burmese line that came back wrong
    #: was wrong in the finished video. Merged so that correcting Burmese cannot wipe a
    #: Japanese track sitting beside it in the same map.
    translations: dict[str, str] | None = None
    #: Where this cue sits in the source video. Editable because a subtitle file being a
    #: second out of sync is the most ordinary thing that can be wrong with one, and a
    #: captioning tool that can fix the words but not when they appear fixes half the problem.
    source_start_ms: int | None = Field(default=None, ge=0)


class BalanceOut(BaseModel):
    available: int
    reserved: int
    committed: int


class QuoteOut(BaseModel):
    """What a plan would cost, before anything runs."""

    credits: int
    per_scene: list[int]


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stage: str
    status: str
    attempts: int
    error: str | None


class UploadOut(BaseModel):
    """What a file turned out to be, measured rather than claimed.

    The Start screen decides which lanes to offer from these numbers: a file with no audio
    cannot be subtitled, and one under a minute is not worth clipping.
    """

    asset_id: uuid.UUID
    kind: str
    mime: str
    bytes: int
    filename: str
    duration_ms: int
    width: int
    height: int
    has_audio: bool
    aspect: str


class RecipeOut(BaseModel):
    """One lane, described for a menu the user never has to read.

    `stages` is included because the storyboard draws the pipeline from it — showing only the
    steps this recipe runs is what makes the five lanes visibly different rather than five
    labels on one progress bar.
    """

    id: str
    stages: list[str]
    needs_source: bool


class NewScene(BaseModel):
    """A scene added by hand to an existing plan.

    Appended by default; `index` inserts. Everything is optional because the storyboard adds
    an empty card first and the user fills it in — demanding a script line up front would
    make "add a scene" a form instead of a button.
    """

    index: int | None = Field(default=None, ge=0)
    prompt: str = ""
    script_line: str = ""
    caption: str | None = None
    duration_ms: int = Field(default=4_000, gt=0, le=60_000)
    #: Where in the source video this line belongs, for the lanes that keep the footage.
    #:
    #: Required in practice for a subtitle track and meaningless for a generated one. Without
    #: it an inserted cue has no moment: the caption stage falls back to the running total of
    #: the scenes before it, which on a lane that hands the video back whole is not a position
    #: in the video at all — so a line added between two others would appear at the wrong time.
    source_start_ms: int | None = Field(default=None, ge=0)


class SceneMove(BaseModel):
    """Reordering. The scene goes to `index`; everything between shifts to make room."""

    index: int = Field(ge=0)


class NewRender(BaseModel):
    """Deliver. One storyboard, up to three aspects."""

    aspects: list[str] = Field(default_factory=lambda: ["9:16"], min_length=1, max_length=3)
    caption_style: str = "pop"
    #: Where the captions sit, as a share of the frame measured up from the bottom edge. The
    #: creator drags this on the preview; 0.16 is where it was hardcoded, which is right for
    #: a talking head and wrong whenever the subject is standing in the lower third.
    caption_bottom: float = Field(default=0.16, ge=0.02, le=0.85)
    #: Caption size, as a multiplier on the default. The default is 5% of the frame's short
    #: edge, which holds the same physical size across every export; this is taste on top.
    caption_scale: float = Field(default=1.0, ge=0.6, le=1.8)


class RenderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    aspect: str
    status: str
    asset_id: uuid.UUID | None


class ChannelOut(BaseModel):
    """The identity kit. Inherited by every new project, which is what makes episode two
    look like episode one without the user configuring anything."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    language: str
    voice_profile_asset_id: uuid.UUID | None
    logo_asset_id: uuid.UUID | None
    identity: dict[str, object]


class ChannelEdit(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    language: str | None = Field(default=None, max_length=16)
    voice_profile_asset_id: uuid.UUID | None = None
    logo_asset_id: uuid.UUID | None = None
    #: Merged into the existing document rather than replacing it, so setting a palette does
    #: not silently drop the caption style.
    identity: dict[str, object] | None = None


class ProviderOut(BaseModel):
    """A sign-in provider, and whether this server can actually use it.

    `enabled` is false when no client id and secret are configured. The screen shows it
    anyway, greyed with a reason — a button that vanishes leaves someone wondering whether
    they misremembered which providers were on offer.
    """

    id: str
    name: str
    enabled: bool


class SignUp(BaseModel):
    """Create an account."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)
    language: str = "en"


class SignIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class SessionOut(BaseModel):
    """Who you are, what you can spend, and the token to carry.

    Returned by sign-up and sign-in alike, so the client has one shape to handle and the two
    screens differ only in which endpoint they call.
    """

    #: Send as `Authorization: Bearer <token>`. Not the user id — that is a database key, and
    #: handing it out as the credential makes every log line a credential leak.
    token: str
    user_id: uuid.UUID
    email: str
    display_name: str | None
    channel_id: uuid.UUID
    language: str
    credits: int


class LedgerEntryOut(BaseModel):
    """One credit movement.

    The balance is folded from these and never stored, so this listing is not a report *about*
    the balance — it is the thing the balance is computed from. That is why a refund appears
    as its own row rather than as an adjustment to the reserve it discharges.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    amount: int
    reason: str | None
    created_at: datetime


class PostCopy(BaseModel):
    """The caption and tags for one platform.

    Per platform, not one blob reused three times: the length that works on TikTok reads as
    truncated on YouTube, and hashtag conventions differ enough to matter.
    """

    platform: str
    text: str
    hashtags: list[str]
    limit: int = Field(description="The platform's caption limit, so the UI can count down")
