"""Test environment.

The suite must not depend on whoever is running it. Settings are read from `.env`, so without
this a developer's local configuration silently changes what the tests mean:

  - `ANTHROPIC_API_KEY` set would make the planner and Script Studio call the real API — from
    the test suite, on the developer's money, on every run.
  - `PROVIDERS_ENABLED` naming a paid provider would do the same for generation.
  - `GOOGLE_CLIENT_ID` set makes "no provider is configured" false, so tests asserting the
    unconfigured behaviour fail on the machine of the person who just configured it — which is
    exactly when they are least likely to suspect their own environment.

So everything that changes behaviour is pinned here, and a test that wants a setting sets it
explicitly. The database URL is the one exception: it is passed in deliberately, and pointing
it somewhere is the whole point of `make test`.
"""

from __future__ import annotations

import os

import pytest

#: Cleared for every test. Anything here reaching a provider, a model or a payment processor
#: from a test run is a bug in the harness, not in the test.
_NEUTRALISED = {
    "anthropic_api_key": "",
    "gemini_api_key": "",
    "fal_api_key": "",
    "replicate_api_token": "",
    "stripe_secret_key": "",
    "stripe_webhook_secret": "",
    "google_client_id": "",
    "google_client_secret": "",
    "github_client_id": "",
    "github_client_secret": "",
    "apple_client_id": "",
    "apple_client_secret": "",
    #: The fake provider only. Never spend money proving the pipeline works.
    "providers_enabled": "fake",
    #: Needs no model and no network, so the transcribing lanes run anywhere.
    #:
    #: Every speech key is blanked alongside it. A developer with a real key in `.env` would
    #: otherwise run a different suite from CI — quietly, and with the tests that assert what
    #: happens when *nothing* is configured failing for a reason that looks like a bug in the
    #: code they just wrote. A test that reaches a vendor is also a test that costs money and
    #: fails on a train.
    "asr_engine": "even-split",
    "groq_api_key": "",
    "elevenlabs_api_key": "",
    "google_credentials": "",
    "google_project": "",
    "seamless_model_dir": "",
    "seamless_python": "",
    "env": "development",
    "public_base_url": "http://127.0.0.1:8000",
    "web_base_url": "http://127.0.0.1:5175",
}


@pytest.fixture
def hears(monkeypatch: pytest.MonkeyPatch):
    """Make the transcriber return known speech, as a real engine would.

    Without an engine the transcriber correctly returns nothing, and the lanes that read a
    video correctly refuse — so every test of what those lanes *do* needs something heard.
    Faking the engine and not the API is the right seam: everything above `Transcriber` is the
    real code path, which is where the interesting behaviour is.
    """

    def install(*lines: str) -> None:
        from lumina.execution import transcribe as tr

        class Heard:
            name = "test-engine"

            async def transcribe(self, audio, *, language, duration_ms):
                # Spread across the duration it is actually given, the way a real engine does.
                # A fixture with its own fixed length makes every test that checks "the
                # subtitles are as long as the video" measure the fixture instead.
                per = max(1, duration_ms // max(1, len(lines)))
                return [tr.Segment(text, i * per, per) for i, text in enumerate(lines)]

        # Matches the real factory's signature, including the language it now routes on —
        # a fixture that lags the thing it fakes fails every test for the wrong reason.
        monkeypatch.setattr(tr, "transcriber", lambda known_text="", language="en": Heard())

    return install


@pytest.fixture(autouse=True)
def _pinned_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the settings a test could otherwise inherit from the developer's `.env`.

    Autouse and function-scoped: `get_settings()` is cached, so this patches the one instance
    everything shares, and monkeypatch restores it after each test — which also means a test
    that sets one of these itself still wins, because it patches after this fixture ran.
    """
    from lumina.config import get_settings

    settings = get_settings()
    #: REAL_TTS=1 keeps the speech key, for the one test that is *about* reaching the engine.
    #: Kept this narrow deliberately: it is a single named key and a single opt-in variable,
    #: not a general "use my .env" escape hatch, so the default suite still cannot spend money
    #: by accident.
    keep = {"gemini_api_key"} if os.environ.get("REAL_TTS") else set()
    for name, value in _NEUTRALISED.items():
        if name in keep:
            continue
        monkeypatch.setattr(settings, name, value, raising=False)
