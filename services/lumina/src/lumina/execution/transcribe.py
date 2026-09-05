"""Speech to timed text.

Same shape as the provider adapter, and for the same reason: nothing above this file knows
which engine is running. The two recipes that start from a file the creator already has —
Subtitles only and Clip my long video — are entirely dependent on this, so the engine behind
it will be swapped, tuned and per-language before anything else in the product is.

Two implementations:

  - `LocalWhisper` runs faster-whisper on the render pool. Real transcription; needs the model
    weights present, which means either a network on first run or a baked image.
  - `EvenSplit` divides the duration evenly and is what runs when no engine is configured. It
    is honest about being a placeholder: it does not invent words, it distributes whatever
    text it was handed, and where it has none it says so.

Where the narration is our own TTS this is not used at all. The script is already known, and
forced alignment against known text is both cheaper and more accurate than transcribing it
back — a distinction that matters most in the languages where ASR is weakest, which are
exactly the ones the language packs exist for.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
import structlog

from lumina.config import get_settings
from lumina.execution.cleanup import NO_SPEECH, collapse_loops, looks_hallucinated
from lumina.execution.speech import split_points
from lumina.intelligence.languages import detect, get_pack

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Segment:
    """One stretch of speech, with when it was said."""

    text: str
    start_ms: int
    duration_ms: int

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


@runtime_checkable
class Transcriber(Protocol):
    """What every engine must offer. No vendor concepts above this line.

    `runtime_checkable` so the seam can be asserted at the boundary: a structural check here
    catches a half-wired engine at startup instead of inside a worker three stages later.
    """

    name: str

    async def transcribe(
        self, audio: Path, *, language: str, duration_ms: int
    ) -> list[Segment]: ...


class NoSpeechError(RuntimeError):
    """Nothing was transcribed.

    Its own type because the two reasons need different answers from the person: no engine is
    configured (an operator fixes that), or an engine ran and heard nothing (they picked the
    wrong file). `configured` says which.
    """

    def __init__(self, *, configured: bool) -> None:
        super().__init__(
            "the transcriber heard no speech"
            if configured
            else "no speech-to-text engine is configured"
        )
        self.configured = configured


class EvenSplit:
    """The no-engine fallback.

    Distributes text it was given across the duration. Given nothing it returns **nothing** —
    not a segment reading "No speech detected", which was the old behaviour and was worse than
    it looks: that string is not empty, so every check for "did we get anything" passed, and it
    became the script. One upload produced a 300-second scene whose narration was the words
    "No speech detected", padded out with filler.

    An empty result is a state the caller has to handle. A plausible-looking sentence is a
    fabrication that travels all the way to the rendered video.
    """

    name = "even-split"

    def __init__(self, known_text: str = "") -> None:
        self._text = known_text

    async def transcribe(self, audio: Path, *, language: str, duration_ms: int) -> list[Segment]:
        sentences = [s.strip() for s in self._text.replace("\n", " ").split(".") if s.strip()]
        if not sentences:
            return []

        # Weighted by length rather than split evenly: an even split visibly lags on a long
        # sentence, and a caption drifting out of sync is the first thing anyone notices.
        total = sum(len(s) for s in sentences)
        out: list[Segment] = []
        at = 0
        for s in sentences:
            share = round(duration_ms * len(s) / total) if total and duration_ms else 2500
            out.append(Segment(f"{s}.", at, max(600, share)))
            at += max(600, share)
        return out


class LocalWhisper:
    """faster-whisper, on the render pool.

    Model id and decode parameters come from the language pack, not from here — reading speed,
    script and the right beam width all vary by language, and hardcoding one engine's defaults
    is exactly the leak `LanguagePack` exists to prevent.

    Loaded lazily and once. The model is hundreds of megabytes and takes seconds to bring up;
    doing that per job would dominate the runtime of a short clip.
    """

    name = "faster-whisper"
    _model: object | None = None

    def __init__(self, compute_type: str = "int8") -> None:
        self._compute_type = compute_type

    def _load(self, model_id: str) -> object:
        if LocalWhisper._model is None:
            from faster_whisper import WhisperModel  # imported here: optional dependency

            LocalWhisper._model = WhisperModel(model_id, compute_type=self._compute_type)
            log.info("transcribe.model.loaded", model=model_id)
        return LocalWhisper._model

    async def transcribe(self, audio: Path, *, language: str, duration_ms: int) -> list[Segment]:
        pack = get_pack(language)
        # Decoding is CPU-bound and blocking. This runs on the render pool, whose whole job is
        # work like this, but it still must not sit on the event loop.
        # The local runner takes a faster-whisper size, not whatever model the language's
        # preferred engine uses — a language that prefers Scribe would otherwise ask
        # faster-whisper to load "scribe_v1". A language with no Whisper model named has none
        # for a reason, and gets refused rather than transcribed by an engine that cannot
        # read it.
        wanted = pack.asr.models.get("whisper")
        if wanted is None:
            raise NoSpeechError(configured=False)
        return await asyncio.to_thread(self._run, audio, _local_size(wanted), language)

    def _run(self, audio: Path, model_id: str, language: str) -> list[Segment]:
        model = self._load(model_id)
        segments, _info = model.transcribe(  # type: ignore[attr-defined]
            str(audio),
            language=language.split("-")[0],
            vad_filter=True,  # drops silence, which otherwise becomes empty caption states
            word_timestamps=False,
        )
        out: list[Segment] = []
        for seg in segments:
            start = round(seg.start * 1000)
            end = round(seg.end * 1000)
            text = seg.text.strip()
            if text:
                out.append(Segment(text, start, max(200, end - start)))
        log.info("transcribe.whisper", segments=len(out), model=model_id)
        return out


#: How much audio goes in one request to a hosted Whisper.
#:
#: Groq caps an upload at 25 MB on the free tier, and mono 16 kHz PCM — what `extract_audio`
#: produces, because it is what every ASR model wants — runs at 1.92 MB a minute. So the wall
#: is about thirteen minutes, and it is not a polite refusal: a larger file returns a 502.
#: Ten minutes leaves room for the container header and for a boundary nudged backwards onto
#: a pause, and stays under the limit on the free tier as well as the paid one.
WHISPER_CHUNK_MS = 10 * 60 * 1000


def _wav_chunks(audio: Path, into: Path) -> list[tuple[Path, int]]:
    """Split a mono 16-bit PCM wav into pieces small enough to send, with their offsets.

    Returns `[(path, offset_ms)]` — one entry, the file itself, when it already fits. The
    offsets are what makes the result usable: each chunk is transcribed as though it began at
    zero, so every timing it reports has to be moved back to where it belongs in the video.
    """
    import wave as wavefile

    try:
        with wavefile.open(str(audio), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            if frames / rate * 1000 <= WHISPER_CHUNK_MS:
                return [(audio, 0)]
            raw = handle.readframes(frames)
    except (wavefile.Error, EOFError):
        # Not a wav this can parse. Send it whole and let the engine judge it — which is what
        # happened before there was any splitting here. Anything `extract_audio` produced is
        # parseable, so in practice this is a caller that skipped it.
        log.debug("transcribe.chunk.unparseable", audio=audio.name)
        return [(audio, 0)]

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)

    edges = [0, *split_points(samples, max_ms=WHISPER_CHUNK_MS), samples.size]
    out: list[tuple[Path, int]] = []
    for i, (a, b) in enumerate(itertools.pairwise(edges)):
        if b <= a:
            continue
        piece = into / f"chunk{i:03d}.wav"
        with wavefile.open(str(piece), "wb") as sink:
            sink.setnchannels(1)
            sink.setsampwidth(width)
            sink.setframerate(rate)
            sink.writeframes((samples[a:b] * 32768.0).astype(np.int16).tobytes())
        out.append((piece, round(a / rate * 1000)))
    return out


class HostedWhisper:
    """Whisper, hosted. OpenAI-compatible, so any provider of that shape works.

    Groq by default because the economics are not close: Whisper large-v3-turbo there runs at
    a couple of hundred times real time for about four cents an hour of audio, against roughly
    thirty-six at OpenAI. A forty-minute lecture costs less than three cents and comes back in
    around ten seconds.

    Segment timings rather than word timings on purpose. Whisper's word timestamps are
    interpolated rather than measured and drift on long files; the caption stage re-times
    within a line anyway, from the language pack's reading speed, so asking for a precision
    the model does not have would only be a slower way to get the same answer.
    """

    name = "whisper"

    async def transcribe(self, audio: Path, *, language: str, duration_ms: int) -> list[Segment]:
        import httpx

        settings = get_settings()
        pack = get_pack(language)
        if not settings.groq_api_key:
            raise NoSpeechError(configured=False)

        out: list[Segment] = []
        with tempfile.TemporaryDirectory(prefix="lumina-asr-") as tmp:
            pieces = await asyncio.to_thread(_wav_chunks, audio, Path(tmp))
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
                for piece, offset_ms in pieces:
                    with piece.open("rb") as fh:
                        reply = await client.post(
                            "https://api.groq.com/openai/v1/audio/transcriptions",
                            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                            files={"file": (piece.name, fh, "audio/wav")},
                            data={
                                # Named, never guessed. Whisper's own language detection is a
                                # coin toss between Chinese and Japanese on short clips, and
                                # being wrong produces fluent output in the wrong script
                                # rather than an error.
                                "model": pack.asr.models["whisper"],
                                "language": pack.asr.decode.get("language", language),
                                "response_format": "verbose_json",
                                "temperature": "0",
                            },
                        )
                    reply.raise_for_status()
                    out.extend(
                        Segment(
                            text=collapse_loops(str(seg.get("text", "")).strip()),
                            # Back to where it belongs in the video: the engine timed this
                            # chunk from its own zero.
                            start_ms=offset_ms + round(float(seg.get("start", 0)) * 1000),
                            duration_ms=max(
                                200,
                                round(
                                    (float(seg.get("end", 0)) - float(seg.get("start", 0))) * 1000
                                ),
                            ),
                        )
                        for seg in reply.json().get("segments", [])
                        if str(seg.get("text", "")).strip()
                        # The model's own verdict on whether anyone was speaking. Over a music
                        # bed it runs high while the words still look confident, which is
                        # exactly the case a reader cannot tell apart from a real caption.
                        and float(seg.get("no_speech_prob", 0.0)) < NO_SPEECH
                        and not looks_hallucinated(str(seg.get("text", "")))
                    )

        log.info(
            "transcribe.hosted",
            engine="groq",
            segments=len(out),
            chunks=len(pieces),
            language=language,
        )
        return out


class Scribe:
    """ElevenLabs Scribe, for the languages Whisper cannot read.

    It exists in this codebase for one reason: Burmese. Published figures put Whisper past
    80% word error there and Scribe near 3% on FLEURS — and the failure mode of a bad ASR on a
    low-resource language is not silence, it is confident fluent nonsense that nobody in the
    room can check. That is worth paying more per hour for.

    Words come back individually timed, so they are grouped into caption-length lines here
    rather than handed on one word at a time.
    """

    name = "scribe"

    #: How long a gap has to be before it starts a new caption line. Below this it is the
    #: pause inside a sentence, not between two.
    GAP_MS = 700
    #: And how long a line may run regardless, so a speaker who does not pause still gets
    #: readable captions rather than one line the length of the video.
    MAX_MS = 6_000

    async def transcribe(self, audio: Path, *, language: str, duration_ms: int) -> list[Segment]:
        import httpx

        settings = get_settings()
        pack = get_pack(language)
        if not settings.elevenlabs_api_key:
            raise NoSpeechError(configured=False)

        with audio.open("rb") as fh:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
                reply = await client.post(
                    "https://api.elevenlabs.io/v1/speech-to-text",
                    headers={"xi-api-key": settings.elevenlabs_api_key},
                    files={"file": (audio.name, fh, "audio/wav")},
                    data={
                        "model_id": pack.asr.models["scribe"],
                        "language_code": pack.asr.decode.get("language", language),
                        "timestamps_granularity": "word",
                    },
                )
        reply.raise_for_status()
        words = [w for w in reply.json().get("words", []) if w.get("type") == "word"]

        out = _into_lines(words, get_pack(language).typography.space_between_units)
        log.info("transcribe.hosted", engine="scribe", segments=len(out), language=language)
        return out


def _into_lines(words: list[dict[str, Any]], spaced: bool | None) -> list[Segment]:
    """Group individually-timed words into caption-length lines.

    Broken on the pauses the speaker actually made, with a ceiling so a speaker who never
    pauses still gets readable lines. Joined with a space only where the script uses one —
    inserting spaces into Burmese or Japanese would be a spelling error, not a formatting
    choice.

    `spaced=None` means nobody has said which language this is: the file was transcribed with
    `AUTO`, and it may legitimately change language part way through. Each line then answers
    the question for itself, from the script it came back in — which is the only way a
    Japanese line and an English line in the same file can both be joined correctly.
    """
    lines: list[Segment] = []
    bucket: list[str] = []
    start = 0.0
    end = 0.0

    def join(parts: list[str]) -> str:
        if spaced is not None:
            return (" " if spaced else "").join(parts)
        #: Detected from the words themselves. Tried without spaces first because that is the
        #: form a spaceless script is written in, and `detect` reads the script, not the gaps.
        code = detect("".join(parts))
        if code is None:
            #: No script won — digits, punctuation, a stray token. Spaces are the safe answer
            #: because they are what every Latin-script line needs and cost nothing here.
            return " ".join(parts)
        return (" " if get_pack(code).typography.space_between_units else "").join(parts)

    def flush() -> None:
        if bucket:
            # Applied here so every word-timed engine gets it, not only the one that happened
            # to report its own doubt. A loop is a loop whoever wrote it.
            said = collapse_loops(join(bucket).strip())
            if looks_hallucinated(said):
                return
            lines.append(
                Segment(
                    text=said,
                    start_ms=round(start * 1000),
                    duration_ms=max(200, round((end - start) * 1000)),
                )
            )

    for word in words:
        text = str(word.get("text", "")).strip()
        if not text:
            continue
        at = float(word.get("start", 0))
        to = float(word.get("end", at))
        gap_ms = (at - end) * 1000
        if bucket and (gap_ms > Scribe.GAP_MS or (to - start) * 1000 > Scribe.MAX_MS):
            flush()
            bucket, start = [], at
        if not bucket:
            start = at
        bucket.append(text)
        end = to
    flush()
    return lines


class GoogleChirp:
    """Google Cloud Speech-to-Text v2.

    Here because Chirp 2 is the only model at a major cloud that lists Burmese at all, and
    because it descends from USM, which was trained on the long tail rather than having it
    fall out of a mostly-English corpus.

    Chirp *3* is the newer model and does not cover Burmese — the model is chosen from the
    language pack for that reason, not pinned here.

    Auth is a service account rather than a key: v2 takes an OAuth bearer, so `google-auth`
    mints one from the credentials file. Imported lazily like every other optional engine, so
    a deployment that does not use Google does not carry the dependency.
    """

    name = "google"

    async def transcribe(self, audio: Path, *, language: str, duration_ms: int) -> list[Segment]:
        import base64

        import httpx

        settings = get_settings()
        pack = get_pack(language)
        if not (settings.google_credentials and settings.google_project):
            raise NoSpeechError(configured=False)

        token = await asyncio.to_thread(_google_token, settings.google_credentials)
        # Read off the loop: this is the creator's whole upload, and a synchronous read of a
        # hundred megabytes inside a coroutine stalls every other job on the worker.
        raw = await asyncio.to_thread(audio.read_bytes)
        where = settings.google_stt_location
        host = "speech.googleapis.com" if where == "global" else f"{where}-speech.googleapis.com"
        recognizer = f"projects/{settings.google_project}/locations/{where}/recognizers/_"

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
            reply = await client.post(
                f"https://{host}/v2/{recognizer}:recognize",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "config": {
                        # Chirp 3 is newer and does not cover Burmese, so the model is the
                        # pack's rather than a constant here.
                        "model": pack.asr.models["google"],
                        # BCP-47 with a region, which is what v2 wants — the pack carries it.
                        "languageCodes": [pack.asr.decode.get("google", language)],
                        "features": {"enableWordTimeOffsets": True},
                        "autoDecodingConfig": {},
                    },
                    "content": base64.b64encode(raw).decode(),
                },
            )
        reply.raise_for_status()

        words: list[dict[str, Any]] = []
        for result in reply.json().get("results", []):
            best = (result.get("alternatives") or [{}])[0]
            for word in best.get("words", []):
                words.append(
                    {
                        "text": word.get("word", ""),
                        "start": _seconds(word.get("startOffset")),
                        "end": _seconds(word.get("endOffset")),
                    }
                )

        out = _into_lines(words, pack.typography.space_between_units)
        log.info("transcribe.hosted", engine="google", segments=len(out), language=language)
        return out


#: Hosted Whisper names its models the way OpenAI does; faster-whisper wants a bare size.
_LOCAL_SIZES = {"whisper-large-v3-turbo": "large-v3", "whisper-large-v3": "large-v3"}


def _local_size(model: str) -> str:
    return _LOCAL_SIZES.get(model, model)


def _seconds(offset: object) -> float:
    """`"3.5s"` — Google's duration format — as a float."""
    text = str(offset or "0s").removesuffix("s")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _google_token(credentials_path: str) -> str:
    """An OAuth bearer for the Speech API, from a service account file.

    Blocking, so callers run it off the event loop. `google-auth` refreshes and caches
    internally, so this is one HTTP round trip an hour rather than one per transcription.
    """
    # Arrives with `google-genai`, which the translator needs, so it is no longer optional.
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    # `google-auth` annotates this constructor's return but not the constructor, so a strict
    # call check cannot see through it. Narrow ignore rather than an override for the whole
    # module, which would also silence real errors here.
    creds = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        credentials_path, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(Request())
    token: str = creds.token
    return token


class VertexGemini:
    """Gemini on Vertex AI, asked to transcribe.

    Not the same thing as Cloud Speech-to-Text with a different URL. Chirp is an ASR model;
    this is a general audio model being asked for a transcript, and on a low-resource language
    the two can differ substantially in either direction — which is exactly why it is a
    separate entry to be measured rather than an alias.

    Timestamps are requested rather than emitted: the model is told to return a JSON array of
    segments with start and end times. That is less trustworthy than an alignment and is
    treated as such — anything unparseable falls back to one segment covering the file, which
    is honest about having no timing rather than inventing one.
    """

    name = "vertex"

    async def transcribe(self, audio: Path, *, language: str, duration_ms: int) -> list[Segment]:
        import base64

        import httpx

        settings = get_settings()
        pack = get_pack(language)
        if not (settings.google_credentials and settings.google_project):
            raise NoSpeechError(configured=False)

        token = await asyncio.to_thread(_google_token, settings.google_credentials)
        raw = await asyncio.to_thread(audio.read_bytes)
        where = settings.vertex_location
        url = (
            f"https://{where}-aiplatform.googleapis.com/v1/projects/"
            f"{settings.google_project}/locations/{where}/publishers/google/models/"
            f"{pack.asr.models.get('vertex', settings.vertex_model)}:generateContent"
        )

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
            reply = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "audio/wav",
                                        "data": base64.b64encode(raw).decode(),
                                    }
                                },
                                {"text": _ASR_PROMPT.format(language=pack.english_name)},
                            ],
                        }
                    ],
                    # Deterministic: this is transcription, and a model free to paraphrase is
                    # a model that will.
                    "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
                },
            )
        reply.raise_for_status()

        body = reply.json()
        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            rows = json.loads(text)
        except (KeyError, IndexError, json.JSONDecodeError):
            log.warning("transcribe.vertex.unparseable", language=language)
            return []

        out: list[Segment] = []
        for row in rows if isinstance(rows, list) else []:
            said = str(row.get("text", "")).strip()
            if not said:
                continue
            start = int(float(row.get("start", 0)) * 1000)
            end = int(float(row.get("end", 0)) * 1000)
            out.append(Segment(said, start, max(200, end - start)))

        log.info("transcribe.hosted", engine="vertex", segments=len(out), language=language)
        return out


_ASR_PROMPT = (
    "Transcribe this audio in {language}, exactly as spoken. Do not translate, summarise, "
    "correct or paraphrase. Return a JSON array of objects with keys `start`, `end` "
    "(seconds, numbers) and `text` (the words said in that span). Break at natural pauses. "
    "If there is no speech, return an empty array."
)


#: "Work it out yourself" — not a language, and deliberately not a pack.
#:
#: A real file is not always in one language: a Japanese video with an English song over it
#: is two, and naming either one mistranscribes the other. This is the absence of an answer,
#: so it never reaches `get_pack`; it selects an engine that can detect, and the languages
#: come back in the transcript itself.
AUTO = "auto"


class Gemini:
    """Gemini 3.5 Transcribe, through the AI Studio API.

    Two things make it worth a slot rather than being a second name for `VertexGemini`.

    *It authenticates with a plain API key.* A deployment that set `GEMINI_API_KEY` for
    translation can already hear speech — no GCP project, no service account, no IAM role.
    For a hosted service that is the difference between transcribing and asking every creator
    to bring their own SRT.

    *It reports word times rather than guessing them.* This is a purpose-built speech model,
    not a chat model asked politely for JSON, so timings arrive as annotations on the words
    themselves. That matters more here than anywhere else in the product: a subtitle is wrong
    if it is late, however right the words are.

    It also covers Burmese (`my-MM`), which most hosted engines do not.
    """

    name = "gemini"

    async def transcribe(self, audio: Path, *, language: str, duration_ms: int) -> list[Segment]:
        from google import genai

        settings = get_settings()
        if not settings.gemini_api_key:
            raise NoSpeechError(configured=False)
        #: `auto` has no pack, by definition. Everything the pack would decide — the model,
        #: the decode hint, whether the script uses spaces — has a defensible default here,
        #: and the transcript arrives in whatever languages were actually spoken.
        pack = None if language == AUTO else get_pack(language)

        client = genai.Client(api_key=settings.gemini_api_key)
        # Uploaded rather than inlined: an hour of 16 kHz mono is far past any inline cap,
        # and uploads expire on their own after 48 hours so there is nothing to clean up.
        handle = await client.aio.files.upload(file=str(audio))

        interaction = await client.aio.interactions.create(
            model=(pack.asr.models.get("gemini") if pack else None) or "gemini-3.5-transcribe",
            # The audio is a *step*, not a bare content block. Passing the block on its own
            # is accepted by the SDK and then rejected by the service as an unknown input
            # type, which is a confusing way to find out.
            input=[
                {
                    "type": "user_input",
                    "content": [
                        {
                            "type": "audio",
                            "uri": handle.uri,
                            # The uploaded file's own mime, not a normalised guess: the Files
                            # API calls a wav `audio/x-wav`, and the service checks what is
                            # declared here against what it stored.
                            "mime_type": handle.mime_type,
                        }
                    ],
                }
            ],
            generation_config={
                "transcription_config": {
                    # Named when the creator named it: detection on a low-resource language is
                    # a coin flip taken for no reason when the answer is already known.
                    #
                    # Omitted entirely for `auto`, which is what makes a code-switched file
                    # work — each stretch comes back in the language it was actually spoken
                    # in, rather than the whole file being forced through one.
                    **(
                        {"language_codes": [pack.asr.decode.get("google", language)]}
                        if pack
                        else {}
                    ),
                    "mode": {"type": "verbatim", "timestamp_granularities": ["word"]},
                }
            },
        )

        words = [
            annotation
            for step in (getattr(interaction, "steps", None) or [])
            for content in (getattr(step, "content", None) or [])
            for annotation in (getattr(content, "annotations", None) or [])
            if getattr(annotation, "type", None) == "word_info"
        ]

        if not words:
            # No timings came back, but the transcript did. One segment covering the file is
            # honest about having no alignment; inventing offsets from character counts is
            # not, and the caller can still edit and re-time it.
            said = (getattr(interaction, "output_text", "") or "").strip()
            log.info("transcribe.hosted", engine="gemini", segments=1 if said else 0, untimed=True)
            return [Segment(said, 0, max(200, duration_ms))] if said else []

        out = _into_lines(
            [
                {
                    "text": str(getattr(w, "text", "")),
                    "start": _seconds(getattr(w, "start_offset", 0)),
                    "end": _seconds(getattr(w, "end_offset", 0)),
                }
                for w in words
            ],
            #: `None` for `auto`: each line decides from its own script, so a Japanese line
            #: and an English line in the same file are both joined correctly.
            pack.typography.space_between_units if pack else None,
        )
        log.info("transcribe.hosted", engine="gemini", segments=len(out), language=language)
        return out


#: Resolved at import, not per call: `Path.resolve` touches the filesystem, and doing that
#: inside a coroutine is the habit that matters for real in a worker.
_WORKER = Path(__file__).resolve().parents[3] / "scripts" / "seamless_worker.py"


class Seamless:
    """SeamlessM4T v2, out of process.

    Meta's open model, and the reason it earns a slot is Burmese: every hosted option is
    either untested on it or reports a figure that is not comparable to any other figure, and
    a model that runs here is a model that can be measured on real audio.

    Two consequences it does not hide:

    *It runs in another interpreter.* torch and the CUDA build live in a conda environment;
    the service does not. So this shells out to `scripts/seamless_worker.py` and speaks JSON
    over a pipe, which keeps a nine-gigabyte dependency out of the API image.

    One process per call, which costs about seven seconds of torch import and model load each
    time. That is the right trade for a subtitle job — one call per video, against a worker
    that would otherwise hold nine gigabytes of VRAM between jobs — but it is why the bench
    reports ten seconds for a four-second clip whose inference took under two.

    *It has no timestamps.* Seamless is sequence-to-sequence — it returns a sentence, not an
    alignment. The worker therefore splits the audio on silence and gives each stretch of
    speech the times it actually occupies. The captions land where someone is speaking, which
    is true, rather than at offsets computed from character counts, which would not be.
    """

    name = "seamless"

    async def transcribe(self, audio: Path, *, language: str, duration_ms: int) -> list[Segment]:
        settings = get_settings()
        if not (settings.seamless_model_dir and settings.seamless_python):
            raise NoSpeechError(configured=False)

        job = json.dumps(
            {
                "model_dir": settings.seamless_model_dir,
                "audio": str(audio),
                "language": language,
            }
        )

        process = await asyncio.create_subprocess_exec(
            settings.seamless_python,
            str(_WORKER),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate(job.encode())
        if process.returncode != 0:
            # The worker's own last line, not a generic failure: it is usually a missing
            # weight file or a CUDA mismatch, and both are fixed by reading it.
            tail = err.decode(errors="replace").strip().splitlines()[-1:] or ["no output"]
            raise RuntimeError(f"seamless worker failed: {tail[0][:200]}")

        body = json.loads(out.decode().strip().splitlines()[-1])
        if "error" in body:
            raise RuntimeError(f"seamless: {body['error']}")

        segments = [
            Segment(
                text=str(seg["text"]).strip(),
                start_ms=int(seg["start_ms"]),
                duration_ms=int(seg["duration_ms"]),
            )
            for seg in body.get("segments", [])
            if str(seg.get("text", "")).strip()
        ]
        log.info(
            "transcribe.local",
            engine="seamless",
            segments=len(segments),
            language=language,
            device=body.get("device"),
        )
        return segments


def can_hear(language: str) -> bool:
    """Whether this deployment can actually transcribe this language.

    Per language, because the answer differs by language on the same server: a box with a Groq
    key reads English and cannot read Burmese, and telling a Burmese creator "speech-to-text
    is available" would send them to a lane that refuses after the upload.
    """
    return not isinstance(transcriber(language=language), EvenSplit)


def transcriber(known_text: str = "", language: str = "en") -> Transcriber:
    """The best engine this deployment has for this language.

    Two things decide it, and neither is a table in this file. The **language pack** ranks the
    engines that can actually read its script — a fact about the language — and the
    **environment** says which of them have keys. The first that is both wanted and available
    wins.

    A language whose engines are all unconfigured falls back to the split rather than to
    another engine: Burmese lists only Scribe, and running Whisper on it instead would return
    a confident, fluent, wrong transcript rather than nothing. Silence is recoverable; a
    plausible lie in a script the creator's audience reads and they do not is not.
    """
    settings = get_settings()
    have: dict[str, Callable[[], Transcriber]] = {}
    if settings.groq_api_key:
        have["whisper"] = HostedWhisper
    if settings.elevenlabs_api_key:
        have["scribe"] = Scribe
    if settings.google_credentials and settings.google_project:
        have["google"] = GoogleChirp
    if settings.gemini_api_key:
        have["gemini"] = Gemini
    if settings.seamless_model_dir and settings.seamless_python:
        have["seamless"] = Seamless
    if settings.google_credentials and settings.google_project:
        have["vertex"] = VertexGemini
    # The local model is opt-in and stands in for hosted Whisper when it is the one running.
    if settings.asr_engine == "whisper" and "whisper" not in have:
        have["whisper"] = LocalWhisper

    if language == AUTO:
        # Ranked by capability rather than by a pack, because there is no pack: only an engine
        # that detects the language itself can be handed a file nobody has classified. Whisper
        # is excluded on purpose — it takes one language for the whole file, which is exactly
        # the assumption `auto` exists to avoid.
        for want in ("gemini", "vertex"):
            make = have.get(want)
            if make is not None:
                return make()
        return EvenSplit(known_text)

    for want in get_pack(language).asr.engines:
        make = have.get(want)
        if make is not None:
            return make()
    return EvenSplit(known_text)
