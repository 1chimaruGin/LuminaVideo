"""Text to speech, and the placement of it against a clock.

Dubbing is not narration. Narration is one long read that the picture is then cut to fit;
a dub is the opposite — the picture is fixed, and every line has to land in the window the
original speaker used. So this module does two jobs that are usually one: it synthesizes
each line, and it decides where in the finished track that line sits.

The second job is the one that makes a dub watchable. A translated line is rarely the same
length as the line it replaces — Burmese runs longer than English about as often as it runs
shorter — and a track built by laying the clips end to end drifts further out of sync with
every line, so by the third minute the voice is answering a question nobody has asked yet.
Each clip is therefore placed at its own cue's timestamp, and a clip too long for its window
is gently sped up rather than allowed to run over the next one.

INVARIANT 1 holds here as everywhere: which model speaks a language, what a preview of it
says, and which voices suit it are all read off the language pack, never branched on here.
"""

from __future__ import annotations

import asyncio
import re
import struct
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from lumina.config import get_settings
from lumina.intelligence.languages import get_pack
from lumina.intelligence.languages.base import Voice

log = structlog.get_logger(__name__)

#: Gemini returns raw little-endian 16-bit mono PCM at this rate. Declared rather than
#: probed because every arithmetic below — offsets, padding, the length of the track — is in
#: samples, and guessing the rate wrong shifts the whole dub rather than failing.
RATE = 24_000
WIDTH = 2

#: How much a line may be sped up to fit its window before we stop trying.
#:
#: Past about 1.5x a voice stops sounding hurried and starts sounding comic, which is worse
#: than a line that runs slightly long — an overrun costs sync, a chipmunk costs the video.
MAX_TEMPO = 1.5

#: How long the ramp to silence is on a line that had to be cut short. Long enough not to
#: click, short enough not to swallow a word.
FADE_MS = 40

#: Lines shorter than their window by less than this are left alone. Re-encoding audio to
#: correct forty milliseconds is audible work for an inaudible gain.
SLACK_MS = 120


class NoVoiceError(RuntimeError):
    """No engine here can speak this language.

    Distinct from a failed request: this is knowable before anything is sent, and the caller
    should say so rather than retry.
    """

    def __init__(self, language: str, *, configured: bool = True) -> None:
        self.language = language
        self.configured = configured
        super().__init__(
            f"no speech engine is configured for {language}"
            if configured
            else "no speech engine is configured"
        )


class EmptyResponseError(RuntimeError):
    """The engine answered without any audio in it.

    Its own type because it is retryable and almost nothing else here is: a bad voice name or
    a malformed request fails the same way every time, and this one succeeds on the next
    attempt.
    """

    def __init__(self, model: str) -> None:
        super().__init__(f"{model} returned no audio")


@dataclass(frozen=True, slots=True)
class Cue:
    """One line, and the moment in the finished video it belongs at."""

    text: str
    start_ms: int
    duration_ms: int


def voices_for(language: str) -> tuple[Voice, ...]:
    """The voices offered for a language, or nothing if it cannot be spoken here."""
    pack = get_pack(language)
    return pack.tts.voices if pack.tts.speakable else ()


def can_speak(language: str) -> bool:
    """Whether a dub can actually be spoken in this language on this server.

    Two conditions, and both have to hold: the pack says an engine covers the language, and
    that engine is configured here. Asked as one question because the screen has one thing to
    decide — whether to offer the voice picker at all — and mirrors `transcribe.can_hear`,
    which answers the same shape of question for listening.
    """
    return bool(voices_for(language)) and bool(get_settings().gemini_api_key)


def sample_text(language: str) -> str:
    """The sentence a voice preview reads, in the language being previewed."""
    return get_pack(language).tts.sample


async def say(text: str, *, language: str, voice: str) -> bytes:
    """One utterance, as raw PCM.

    Returns the samples rather than a file because the caller is assembling a timeline out of
    them and every write to disk in between is a temp file to clean up and a decode to redo.
    """
    from google import genai
    from google.genai import types

    settings = get_settings()
    if not settings.gemini_api_key:
        raise NoVoiceError(language, configured=False)

    pack = get_pack(language)
    if not pack.tts.speakable:
        raise NoVoiceError(language)

    client = genai.Client(api_key=settings.gemini_api_key)
    #: The primary, then whatever the pack says can stand in for it. Quota is counted per
    #: model, so this is a second allowance rather than a second attempt at the same one —
    #: which is the difference between a dub that finishes and one that stops half way with
    #: "that voice could not be prepared".
    chain = (
        pack.tts.models.get("gemini", "gemini-3.1-flash-tts-preview"),
        *pack.tts.alternates,
    )

    exhausted: Exception | None = None
    silent = False
    #: Skip what is known to be out of quota. Without this every line in a dub pays a round
    #: trip to a model that has already refused — thirty-six wasted requests on a thirty-six
    #: line video, each one a chance to be counted against the allowance again.
    usable = [m for m in chain if not _is_spent(m)] or [chain[-1]]
    for model in usable:
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=pack.normalize(text),
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                        )
                    ),
                ),
            )
        except Exception as exc:
            #: Only quota moves us down the chain. A bad voice name or a malformed request
            #: fails identically on every model, and walking the whole list to discover that
            #: turns one clear error into three slow ones.
            if not is_quota(exc):
                raise
            exhausted = exc
            _mark_spent(model, _retry_after(exc) or 60.0)
            if model == usable[-1]:
                break
            log.info(
                "speak.exhausted", model=model, falling_back_to=usable[usable.index(model) + 1]
            )
            continue

        spoken = _audio_of(response)
        if spoken is not None:
            return spoken
        #: An answer with no audio in it. Measured on Burmese against the same model and the
        #: same line, this succeeds on the next attempt three times out of three — so it is a
        #: transient, not a refusal, and reporting it as a failure would give up on a line the
        #: engine was perfectly willing to speak.
        log.info("speak.no_audio", model=model, language=language)
        silent = True

    if silent:
        raise EmptyResponseError(chain[0])
    if exhausted is not None:
        raise exhausted
    raise EmptyResponseError(chain[0])


def _audio_of(response: Any) -> bytes | None:
    """The samples in a response, or None if it carried none.
    Walked rather than indexed. A refusal comes back as a well-formed response with no audio
    in it — `finish_reason` OTHER and an empty `parts` — and `candidates[0].content.parts[0]`
    raises a `TypeError` from inside the SDK on that shape, which reads like a bug in this
    file rather than a line the engine declined to read.
    """
    for candidate in response.candidates or ():
        for part in (candidate.content.parts if candidate.content else None) or ():
            if part.inline_data and part.inline_data.data:
                return bytes(part.inline_data.data)
    return None


async def narrate(
    lines: list[str], *, language: str, voice: str, gap_ms: int = 220
) -> tuple[bytes, list[int]]:
    """Speak lines one after another, and report how long each one took.

    The counterpart to `dub`, and the opposite problem. `dub` is given a clock and has to fit
    speech into it. Here there is no clock until the speech exists: a line is as long as it
    takes to say, and those lengths *become* the cue timings. So this returns the durations
    alongside the audio, because the caller cannot lay out the video without them.

    A short silence follows each line and is counted as part of it, so the cues stay
    contiguous — a caption should still be on screen during the breath after its sentence,
    not blink off into a gap the viewer reads as a glitch.
    """
    if not lines:
        raise ValueError("nothing to narrate")

    pace = _Pace(get_settings().speech_rpm)

    async def one(text: str) -> bytes:
        return await _with_retry(lambda: say(text, language=language, voice=voice), pace)

    clips = await asyncio.gather(*(one(line) for line in lines))

    breath = b"\x00\x00" * _samples(gap_ms)
    track = bytearray()
    spans: list[int] = []
    for clip in clips:
        track += clip + breath
        spans.append(len(clip) // WIDTH * 1000 // RATE + gap_ms)

    log.info("speak.narrate", lines=len(lines), voice=voice, language=language, ms=sum(spans))
    return wav(bytes(track)), spans


async def dub(cues: list[Cue], *, language: str, voice: str, total_ms: int) -> bytes:
    """Every line spoken and placed, returned as one WAV.

    Lines are synthesized concurrently and then written into a single silent buffer at their
    own timestamps. Building the buffer this way rather than with an ffmpeg filter graph is
    not a micro-optimisation: a 274-line video is 274 `adelay` filters and one `amix`, which
    is a command line long enough to hit the argument limit, and the arithmetic here is
    exact — a sample offset is an integer, not a rounded filter argument.
    """
    if not cues:
        raise ValueError("nothing to say")

    #: Paced, not merely bounded. The speech API's quota is counted in *requests per minute*
    #: and the free tier allows ten; a dub is one request per line, so a thirty-six line video
    #: is three and a half times over the limit before the first minute is out. Running four
    #: at a time simply reached the wall sooner — the whole preview failed with "that voice
    #: could not be prepared", which reads as a broken voice rather than as a quota.
    pace = _Pace(get_settings().speech_rpm)

    async def one(cue: Cue) -> bytes:
        return await _with_retry(lambda: say(cue.text, language=language, voice=voice), pace)

    clips = await asyncio.gather(*(one(c) for c in cues))

    track = bytearray(_samples(total_ms) * WIDTH)
    trimmed = 0

    #: Each line's room is up to where the next one starts, never past it.
    #:
    #: Speeding a clip up is capped at MAX_TEMPO, so a line can still come back longer than
    #: its window. Writing it anyway used to run it into the next line's slot, where the next
    #: clip then overwrote it — two voices colliding, the first cut off mid-word with a click
    #: at the join. A line that will not fit is faded out inside its own window instead: still
    #: a compromise, but one that sounds like a decision rather than a fault.
    for i, (cue, clip) in enumerate(zip(cues, clips, strict=True)):
        #: Room is the distance to the *next line*, not the length of this cue's own window.
        #: The last line has no next line, so it may run on to the end of the video — clamping
        #: it to its cue would cut the closing sentence off for a collision that cannot happen.
        room_ms = (
            cues[i + 1].start_ms - cue.start_ms if i + 1 < len(cues) else total_ms - cue.start_ms
        )
        room_ms = max(0, room_ms)

        fitted = await _fit(clip, room_ms)
        at = _samples(cue.start_ms) * WIDTH
        if at >= len(track):
            continue

        ceiling = min(at + _samples(room_ms) * WIDTH, len(track))
        if at + len(fitted) > ceiling:
            fitted = _faded(fitted[: ceiling - at])
            trimmed += 1

        track[at : at + len(fitted)] = fitted

    log.info("speak.dub", lines=len(cues), voice=voice, language=language, trimmed=trimmed)
    return wav(bytes(track))


#: Models known to be out of quota, and when it is worth asking them again. Per process and
#: deliberately short-lived: the allowance may be per minute or per day and the error does not
#: always say which, so this is a way to stop hammering a closed door rather than a record of
#: when it opens.
_SPENT: dict[str, float] = {}


def _is_spent(model: str) -> bool:
    until = _SPENT.get(model)
    if until is None:
        return False
    if time.monotonic() >= until:
        del _SPENT[model]
        return False
    return True


def _mark_spent(model: str, seconds: float) -> None:
    _SPENT[model] = time.monotonic() + min(max(seconds, 5.0), 120.0)


class _Pace:
    """Lets at most `rpm` calls start in any sixty-second window.

    A sliding window rather than a token bucket because that is what the quota actually is:
    the service counts requests in the last minute, so spacing them evenly would be slower
    than necessary and bursting to the limit and then waiting is exactly right.
    """

    def __init__(self, rpm: int) -> None:
        self.rpm = max(1, rpm)
        self.started: deque[float] = deque()
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        while True:
            async with self.lock:
                now = time.monotonic()
                while self.started and now - self.started[0] >= 60:
                    self.started.popleft()
                if len(self.started) < self.rpm:
                    self.started.append(now)
                    return
                #: How long until the oldest call falls out of the window.
                sleep_for = 60 - (now - self.started[0]) + 0.05
            await asyncio.sleep(sleep_for)


async def _with_retry(call: Callable[[], Awaitable[bytes]], pace: _Pace) -> bytes:
    """One synthesis, waiting its turn and retrying if the service says to.

    Quota is shared with everything else using the same key, so pacing alone cannot guarantee
    a request is allowed — a translation running at the same time can put us over. The service
    says how long to wait when it refuses; that number is used rather than a guess, because
    guessing short means being refused again and guessing long wastes the minute.
    """
    for attempt in range(_RETRIES):
        await pace.wait()
        try:
            return await call()
        except EmptyResponseError:
            #: Measured as transient — see `say`. A short pause rather than the quota's, which
            #: would spend a minute waiting on something that is not a quota problem.
            if attempt == _RETRIES - 1:
                raise
            log.info("speak.retry_empty", attempt=attempt + 1)
            await asyncio.sleep(1.5)
        # Broad on purpose: the SDK raises one class for every HTTP error, so what a failure
        # *is* has to be read off the message either way — see `_retry_after`.
        except Exception as exc:
            after = _retry_after(exc) or _server_wobble(exc)
            if after is None or attempt == _RETRIES - 1:
                raise
            log.info("speak.throttled", wait_s=round(after, 1), attempt=attempt + 1)
            await asyncio.sleep(after)
    raise RuntimeError("unreachable")


def is_quota(exc: BaseException) -> bool:
    """Whether this failure was the per-minute request allowance running out.

    Public because the API says something different for it: every other failure is ours to
    explain, and this one is the creator's key to raise.
    """
    return _retry_after(exc) is not None


def _server_wobble(exc: BaseException) -> float | None:
    """A short wait if the service failed on its own account, None otherwise.

    Separate from quota because it means something different and is handled differently: the
    allowance has not been spent, the request simply did not land, and the service's own
    advice is to try again. Seen for real mid-dub — a single 500 with thirty-five lines
    already synthesized would otherwise throw all of them away.
    """
    text = str(exc)
    return 2.0 if any(code in text for code in ("500 INTERNAL", "502 ", "503 ", "504 ")) else None


def _retry_after(exc: BaseException) -> float | None:
    """Seconds to wait, if this is a rate limit. None if it is any other failure.

    Read out of the message rather than the exception type: the SDK raises one class for every
    4xx, so the status is the only thing that distinguishes "slow down" from "that voice does
    not exist", and retrying the second forever would hang the request.
    """
    text = str(exc)
    if "429" not in text and "RESOURCE_EXHAUSTED" not in text:
        return None
    found = re.search(r"retry in ([0-9.]+)s", text) or re.search(
        r"retryDelay'?: '?([0-9.]+)s", text
    )
    return min(float(found.group(1)) + 1 if found else 30.0, 65.0)


#: Enough to ride out a couple of quota windows, and not so many that a genuinely failing
#: request takes minutes to report itself.
_RETRIES = 4


def _faded(pcm: bytes) -> bytes:
    """The same samples with the last few milliseconds ramped to silence.

    Cutting PCM mid-waveform steps the signal to zero in one sample, which is heard as a click
    — and a click at the end of every over-long line is more noticeable than the truncation
    it comes from.
    """
    ramp = min(_samples(FADE_MS), len(pcm) // WIDTH)
    if ramp <= 0:
        return pcm

    out = bytearray(pcm)
    start = len(out) - ramp * WIDTH
    for i in range(ramp):
        at = start + i * WIDTH
        sample = int.from_bytes(out[at : at + WIDTH], "little", signed=True)
        faded = int(sample * (1 - (i + 1) / ramp))
        out[at : at + WIDTH] = faded.to_bytes(WIDTH, "little", signed=True)
    return bytes(out)


async def _fit(pcm: bytes, window_ms: int) -> bytes:
    """Speed a clip up if it overruns its window, within reason.

    Only ever faster, never slower. A line that comes in short leaves a gap, and a gap in a
    dub reads as a pause the speaker took; a line that comes in long talks over the next one,
    which reads as a bug.
    """
    spoken_ms = len(pcm) // WIDTH * 1000 // RATE
    if window_ms <= 0 or spoken_ms <= window_ms + SLACK_MS:
        return pcm

    tempo = min(spoken_ms / window_ms, MAX_TEMPO)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-v",
        "error",
        "-f",
        "s16le",
        "-ar",
        str(RATE),
        "-ac",
        "1",
        "-i",
        "pipe:0",
        "-filter:a",
        f"atempo={tempo:.4f}",
        "-f",
        "s16le",
        "-ar",
        str(RATE),
        "-ac",
        "1",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate(pcm)
    #: A failed speed-up returns the original. The line runs long, which is a flaw; losing it
    #: entirely would be a hole in the dub, which is a defect.
    return out if proc.returncode == 0 and out else pcm


def _samples(ms: int) -> int:
    return max(0, ms) * RATE // 1000


def wav(pcm: bytes) -> bytes:
    """A RIFF header around the samples. Written here rather than through `wave` because the
    caller wants bytes, and `wave` insists on a file object it can seek."""
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * WIDTH, WIDTH, 8 * WIDTH)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )
