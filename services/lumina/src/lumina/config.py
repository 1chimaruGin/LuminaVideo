"""Settings. One source of truth, validated at import."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The local default. Named so the production check can recognise it by identity rather than
#: by guessing at what "looks insecure".
DEV_SECRET = "dev-only-insecure"

#: HS256 keys shorter than the hash are weaker than the hash. RFC 7518 §3.2.
MIN_SECRET_BYTES = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"), env_prefix="", extra="ignore"
    )

    env: Literal["development", "staging", "production"] = Field(
        default="development", alias="LUMINA_ENV"
    )
    #: Signs session tokens. The default is fine locally and must never reach production —
    #: anyone holding it can mint a token for any account.
    secret_key: str = Field(default=DEV_SECRET, alias="LUMINA_SECRET_KEY")

    #: Defaults to the local cluster `make up` creates — port 55432, not 5432, so it can
    #: never collide with a system PostgreSQL that happens to be running. Without this the
    #: app silently migrates and queries whatever is on the default port.
    database_url: str = Field(
        default="postgresql+asyncpg://lumina@127.0.0.1:55432/lumina",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    #: "local" writes to disk so a job can run with no infrastructure at all; "s3" is R2.
    storage: str = Field(default="local", alias="STORAGE")
    local_media_root: str = Field(default=".data/media", alias="LOCAL_MEDIA_ROOT")

    #: Upload ceiling. A phone shooting 4K makes ~350 MB/minute, so a long video a creator
    #: wants clipped is realistically a couple of gigabytes. Enforced by counting bytes as
    #: they stream in, never by trusting Content-Length.
    max_upload_bytes: int = Field(default=4 * 1024**3, alias="MAX_UPLOAD_BYTES")

    s3_endpoint_url: str = Field(default="http://localhost:9000", alias="S3_ENDPOINT_URL")
    s3_bucket: str = Field(default="lumina-assets", alias="S3_BUCKET")
    s3_access_key_id: str = Field(default="lumina", alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str = Field(default="lumina-dev-secret", alias="S3_SECRET_ACCESS_KEY")
    s3_region: str = Field(default="auto", alias="S3_REGION")
    s3_public_base_url: str = Field(
        default="http://localhost:9000/lumina-assets", alias="S3_PUBLIC_BASE_URL"
    )

    #: Comma-separated. Defaults to the fake provider so tests never spend money.
    providers_enabled: str = Field(default="fake", alias="PROVIDERS_ENABLED")
    fal_api_key: str = Field(default="", alias="FAL_API_KEY")
    replicate_api_token: str = Field(default="", alias="REPLICATE_API_TOKEN")

    #: Our own mark, burnt into a render that has not been told to use something else.
    #:
    #: A path rather than an asset row: it ships with the code, it is the same file for every
    #: account, and a row in the database would be one more thing to seed on a fresh install.
    watermark_logo: str = Field(default="", alias="WATERMARK_LOGO")

    #: How hard x264 works. See `compose.ffmpeg.video_codec` for the measurements behind the
    #: defaults: `superfast` at crf 23 renders in a little over half the time of `veryfast` at
    #: crf 20 and lands on the same file size.
    ffmpeg_preset: str = Field(default="superfast", alias="FFMPEG_PRESET")
    ffmpeg_crf: int = Field(default=23, alias="FFMPEG_CRF")

    #: Which speech-to-text engine to run. "whisper" needs faster-whisper installed and the
    #: model weights present; the default needs neither, so the pipeline runs anywhere.
    asr_engine: str = Field(default="even-split", alias="ASR_ENGINE")
    #: Hosted Whisper. The cheapest good option for the well-served languages by a wide
    #: margin — see `execution/transcribe.py` for why that is not the whole answer.
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    #: ElevenLabs Scribe. Strong on low-resource languages by its own numbers.
    elevenlabs_api_key: str = Field(default="", alias="ELEVENLABS_API_KEY")
    #: Google Cloud Speech-to-Text v2. Its Chirp 2 is the only major cloud model that lists
    #: Burmese, and the USM family it comes from was built for exactly these languages.
    #: Service-account JSON path, and the project the recognizer lives in.
    google_credentials: str = Field(default="", alias="GOOGLE_APPLICATION_CREDENTIALS")
    google_project: str = Field(default="", alias="GOOGLE_CLOUD_PROJECT")
    google_stt_location: str = Field(default="global", alias="GOOGLE_STT_LOCATION")
    #: SeamlessM4T v2, run locally. Open weights, so it is the one engine whose Burmese can be
    #: measured rather than taken on a vendor's word. Path to the weights, and the interpreter
    #: that has torch — usually a conda env, because the service runs on a different Python.
    seamless_model_dir: str = Field(default="", alias="SEAMLESS_MODEL_DIR")
    seamless_python: str = Field(default="", alias="SEAMLESS_PYTHON")
    #: Gemini on Vertex AI. A different engine from Cloud Speech-to-Text, not a different
    #: endpoint for it: a general audio model asked to transcribe, rather than an ASR model.
    #: Worth measuring separately, because on a low-resource language the two can differ a
    #: lot in both directions. Shares the service account with Cloud STT.
    vertex_model: str = Field(default="gemini-2.5-flash", alias="VERTEX_MODEL")
    vertex_location: str = Field(default="us-central1", alias="VERTEX_LOCATION")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    #: Gemini, from AI Studio — a plain API key, not the service-account JSON that Cloud
    #: Speech and Vertex need. It is here for translation specifically: on BURMESE-SAN, the
    #: native-speaker-built benchmark, Gemini Flash scores 89.5 on Burmese translation against
    #: 87.5 for Claude Opus 4.1 and 87.5 for GPT-5, and bills per token rather than per
    #: character, which at subtitle volumes is roughly a fourteenth of Cloud Translation.
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    #: An alias rather than a pin, so the model improves without a deploy. Pin it here if a
    #: release ever regresses — the engine logs which version actually answered.
    gemini_model: str = Field(default="gemini-flash-latest", alias="GEMINI_MODEL")
    #: Speech requests allowed per minute, per model.
    #:
    #: The speech API is quota'd per *request*, not per character, and the free tier allows
    #: ten a minute. A dub is one request per line, so a thirty-six line video is nearly four
    #: minutes of waiting on quota alone — and firing them all at once does not make it
    #: faster, it makes it fail. Configurable because a paid key raises this by orders of
    #: magnitude and there is no way to discover the limit except by being told.
    speech_rpm: int = Field(default=10, alias="SPEECH_RPM")
    planner_model: str = Field(default="claude-opus-5", alias="PLANNER_MODEL")

    #: Comma-separated. Only consulted in production; development allows any loopback origin.
    cors_allowed_origins: str = Field(default="", alias="CORS_ALLOWED_ORIGINS")

    # ---- OAuth ----
    #
    # Empty means "not configured", and the provider is offered as disabled rather than as a
    # button that fails after a round trip to Google. Register the app with each provider and
    # set the redirect URI to {public_base_url}/auth/oauth/{provider}/callback.
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")
    github_client_id: str = Field(default="", alias="GITHUB_CLIENT_ID")
    github_client_secret: str = Field(default="", alias="GITHUB_CLIENT_SECRET")
    apple_client_id: str = Field(default="", alias="APPLE_CLIENT_ID")
    #: Apple's "secret" is a short-lived ES256 JWT you generate from your .p8 key, not a
    #: static string. Pre-generate it and rotate it; it expires in at most six months.
    apple_client_secret: str = Field(default="", alias="APPLE_CLIENT_SECRET")

    #: Where this API is reachable from the browser. The redirect URI is built from it, and
    #: it must match what the provider has registered exactly — a trailing slash is a mismatch.
    public_base_url: str = Field(default="http://127.0.0.1:8000", alias="PUBLIC_BASE_URL")
    #: Where to send the browser back to once signed in.
    #:
    #: Must be the port the web app is actually served on — `apps/web/vite.config.ts` pins it
    #: to 5173. This defaulted to 5175, which nothing listens on: in development the browser
    #: sends its own origin and `safe_next` accepts any loopback address, so the mismatch stayed
    #: invisible there and only bit where no `next` is sent, and in production, which accepts
    #: this value and nothing else.
    web_base_url: str = Field(default="http://127.0.0.1:5173", alias="WEB_BASE_URL")

    stripe_secret_key: str = Field(default="", alias="STRIPE_SECRET_KEY")
    stripe_webhook_secret: str = Field(default="", alias="STRIPE_WEBHOOK_SECRET")
    sentry_dsn: str = Field(default="", alias="SENTRY_DSN")

    @model_validator(mode="after")
    def _production_needs_a_real_secret(self) -> Settings:
        """Refuse to start rather than sign tokens with a key anyone can read.

        Checked at import, not at first use: a deployment that would mint forgeable sessions
        should fail on the way up, where someone is watching, not on the first sign-in.
        """
        if self.env != "production":
            return self
        if self.secret_key == DEV_SECRET:
            raise ValueError(
                "LUMINA_SECRET_KEY is still the development default; "
                "generate one with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`"
            )
        if len(self.secret_key.encode()) < MIN_SECRET_BYTES:
            raise ValueError(
                f"LUMINA_SECRET_KEY must be at least {MIN_SECRET_BYTES} bytes "
                f"(RFC 7518 §3.2); got {len(self.secret_key.encode())}"
            )
        return self

    @property
    def providers(self) -> list[str]:
        return [p.strip() for p in self.providers_enabled.split(",") if p.strip()]

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    def oauth_credentials(self, provider: str) -> tuple[str, str]:
        """(client_id, client_secret) for a provider, or empty strings if unconfigured."""
        pairs = {
            "google": (self.google_client_id, self.google_client_secret),
            "github": (self.github_client_id, self.github_client_secret),
            "apple": (self.apple_client_id, self.apple_client_secret),
        }
        return pairs.get(provider, ("", ""))

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
