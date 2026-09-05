"""Stages and jobs.

Stages are pure functions producing immutable artifacts. Re-running a stage with the same
inputs must produce the same artifact key and must not double-charge (invariant 3).
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any


class Stage(StrEnum):
    """ingest -> transcribe -> analyze -> plan -> [per-scene: generate] -> voice -> captions
    -> compose

    Not every recipe runs every stage; `RECIPE_STAGES` below says which. Subtitles only, for
    instance, never plans and never generates — it transcribes what is already there.
    """

    INGEST = "ingest"
    TRANSCRIBE = "transcribe"
    ANALYZE = "analyze"
    PLAN = "plan"
    GENERATE = "generate"  # per scene
    VOICE = "voice"
    CAPTIONS = "captions"
    COMPOSE = "compose"


#: Which worker pool runs each stage. Mixing these is the classic mistake that triples cost.
class Pool(StrEnum):
    LIGHT = "light"
    CPU_RENDER = "cpu-render"
    GPU = "gpu"  # unused in v1: nothing is self-hosted yet


STAGE_POOL: dict[Stage, Pool] = {
    Stage.INGEST: Pool.LIGHT,
    # Transcription is a model call, not local compute: the ASR runs behind an adapter like
    # any other provider, so this pool waits on IO rather than saturating a core.
    Stage.TRANSCRIBE: Pool.LIGHT,
    Stage.ANALYZE: Pool.LIGHT,
    Stage.PLAN: Pool.LIGHT,
    Stage.GENERATE: Pool.LIGHT,  # we poll a vendor; we do not compute
    Stage.VOICE: Pool.LIGHT,
    Stage.CAPTIONS: Pool.CPU_RENDER,  # chromium rasterization
    Stage.COMPOSE: Pool.CPU_RENDER,  # ffmpeg
}


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})

JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    # RUNNING -> QUEUED is a redelivery after a worker died mid-flight, not a retry.
    JobStatus.RUNNING: frozenset(
        {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED}
    ),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),  # explicit retry
    JobStatus.CANCELLED: frozenset(),
}


class IllegalJobTransitionError(ValueError):
    def __init__(self, frm: JobStatus, to: JobStatus) -> None:
        super().__init__(f"job cannot move {frm.value} -> {to.value}")


def assert_job_transition(frm: JobStatus, to: JobStatus) -> None:
    if to not in JOB_TRANSITIONS[frm]:
        raise IllegalJobTransitionError(frm, to)


def idempotency_key(stage: Stage, inputs: dict[str, Any]) -> str:
    """Content-address the *work*, not the request.

    Two enqueues with identical inputs collapse to one job via a unique constraint on this
    key. `sort_keys` is load-bearing: an unsorted dict produces a different digest for
    identical work and silently re-runs — and re-charges — the stage.
    """
    payload = json.dumps(inputs, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(f"{stage.value}:{payload}".encode()).hexdigest()
    return f"{stage.value}:{digest[:32]}"


#: Which stages each recipe actually runs, in order.
#:
#: This map is the difference between "five recipes" and "one pipeline with five labels". The
#: pipeline is fixed — transcribe, ask a model what matters, caption, render — but each lane
#: enters it at a different point and leaves out what it does not need. Subtitles only never
#: plans and never generates a frame; Dub ingests a finished video and only replaces what is
#: heard on it; Clip finds its own moments and reuses the source footage rather than drawing
#: new.
#:
#: Deriving the sequence from data rather than branching on the recipe in each caller is what
#: keeps a sixth recipe a table entry instead of a rewrite.
RECIPE_STAGES: dict[str, tuple[Stage, ...]] = {
    "explainer": (Stage.PLAN, Stage.GENERATE, Stage.VOICE, Stage.CAPTIONS, Stage.COMPOSE),
    # A dub is the creator's own video with a different voice on it, so it starts from a file
    # and keeps every frame. The words are not invented either — they are heard (TRANSCRIBE),
    # translated and laid out as cues (CAPTIONS), and only then spoken (VOICE), which is why
    # VOICE runs after CAPTIONS here and before it everywhere else: the other lanes narrate a
    # script they authored, this one narrates a translation that does not exist until CAPTIONS
    # has produced it.
    "dub": (Stage.INGEST, Stage.TRANSCRIBE, Stage.CAPTIONS, Stage.VOICE, Stage.COMPOSE),
    # Nothing is listened to and nothing is drawn: the creator writes the words, so the plan
    # is their script split into lines, VOICE says them, and the footage they picked runs
    # underneath. No TRANSCRIBE because there is no speech to hear yet — the speech is the
    # output. No GENERATE because the pictures are theirs already.
    "narrate": (Stage.PLAN, Stage.VOICE, Stage.CAPTIONS, Stage.COMPOSE),
    # Starts from a file and keeps its footage: analyze picks the moments, and the source
    # frames are what gets cut, so there is no GENERATE and no VOICE.
    "clip_long_video": (
        Stage.INGEST,
        Stage.TRANSCRIBE,
        Stage.ANALYZE,
        Stage.CAPTIONS,
        Stage.COMPOSE,
    ),
    # The cheapest lane in the product, and the one with the clearest job: the video is
    # finished, it just cannot be watched on mute.
    "subtitle_only": (Stage.INGEST, Stage.TRANSCRIBE, Stage.CAPTIONS, Stage.COMPOSE),
}


def stages_for(recipe: str) -> tuple[Stage, ...]:
    """The stage sequence for a recipe, or the Explainer default for an unknown one.

    Falling back rather than raising is deliberate: an unrecognised recipe should degrade to
    the full pipeline, which is always correct if wasteful, rather than fail a user's upload.
    """
    return RECIPE_STAGES.get(recipe, RECIPE_STAGES["explainer"])
