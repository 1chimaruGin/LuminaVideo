"""Script Studio endpoints.

Everything here is cheap on purpose. Timing costs nothing and runs locally; hooks are a single
small call; a rewrite touches one line. The expensive stage is generation, and the entire point
of this screen is that the script is settled before any of it runs.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from lumina.api.deps import CurrentUser
from lumina.api.schemas import PostCopy
from lumina.intelligence import script as studio

router = APIRouter(prefix="/script", tags=["script"])


class ScriptIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    language: str = "en"
    target_ms: int = Field(default=60_000, ge=5_000, le=180_000)


class RewriteIn(BaseModel):
    line: str = Field(min_length=1, max_length=2_000)
    operation: str
    language: str = "en"


class BeatOut(BaseModel):
    beat: str
    text: str
    duration_ms: int


class BeatsOut(BaseModel):
    beats: list[BeatOut]
    timing: studio.Timing


class RewriteOut(BaseModel):
    line: str


@router.post("/timing", response_model=studio.Timing)
async def read_timing(body: ScriptIn) -> studio.Timing:
    """How long this takes to say, in this language.

    No auth and no model call: it is arithmetic over the language pack's reading speed, and
    it is meant to be called while the user is typing.
    """
    return studio.timing(body.text, language=body.language, target_ms=body.target_ms)


@router.post("/beats", response_model=BeatsOut)
async def beats(body: ScriptIn) -> BeatsOut:
    """The script as a structure, with its length.

    Returned together because they are one question — a creator looking at their beats
    immediately wants to know whether they fit.
    """
    parts = studio.split_beats(body.text, language=body.language, target_ms=body.target_ms)
    return BeatsOut(
        beats=[BeatOut(**p) for p in parts],
        timing=studio.timing(body.text, language=body.language, target_ms=body.target_ms),
    )


@router.post("/hooks", response_model=studio.Hooks)
async def hooks(body: ScriptIn, user: CurrentUser) -> studio.Hooks:
    """Five openings for the same script, each labelled by the pattern it uses."""
    return await studio.hooks(body.text, language=body.language)


@router.post("/rewrite", response_model=RewriteOut)
async def rewrite(body: RewriteIn, user: CurrentUser) -> RewriteOut:
    """One small operation on one line."""
    try:
        line = await studio.rewrite(body.line, body.operation, language=body.language)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    return RewriteOut(line=line)


class PostCopyIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    script: str = Field(min_length=1, max_length=20_000)
    language: str = "en"
    platforms: list[str] | None = None


@router.post("/post-copy", response_model=list[PostCopy])
async def post_copy(body: PostCopyIn, user: CurrentUser) -> list[PostCopy]:
    """The caption and tags for each platform the creator is posting to.

    Per platform rather than one blob reused three times: what fits TikTok is truncated into
    the title on Shorts, and the limit is returned alongside so the UI can count down against
    the real number instead of a hardcoded guess.
    """
    drafts = await studio.post_copy(
        body.title, body.script, language=body.language, platforms=body.platforms
    )
    return [
        PostCopy(
            platform=d.platform,
            text=d.text,
            hashtags=d.hashtags,
            limit=studio.PLATFORMS.get(d.platform, (2200, ""))[0],
        )
        for d in drafts
    ]


@router.get("/rewrites", response_model=dict[str, str])
async def rewrites() -> dict[str, str]:
    """The operations available, and what each one does.

    Served rather than hardcoded in the client so the two cannot drift — a button offering an
    operation the backend does not know is a 400 the user cannot act on.
    """
    return studio.REWRITES
