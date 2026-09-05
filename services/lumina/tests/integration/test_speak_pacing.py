"""Pacing speech against a per-minute request quota.

The speech API counts *requests*, not characters, and the free tier allows ten a minute. A dub
is one request per line, so a thirty-six line video is three and a half times over the limit
inside the first minute. Firing them all at once does not fail fast and clearly — it fails part
way through, after the creator has waited, with a message about the voice.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from lumina.execution import speak


class _VendorError(Exception):
    """Shaped like the SDK's error, which is what `_retry_after` actually reads."""


def test_a_quota_refusal_is_told_apart_from_a_real_failure() -> None:
    """Retrying "that voice does not exist" forever would hang the request, and giving up on
    "slow down" wastes a dub that was going to succeed."""
    quota = _VendorError(
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your "
        "current quota... Please retry in 29.5s', 'status': 'RESOURCE_EXHAUSTED'}}"
    )
    assert speak.is_quota(quota)
    assert not speak.is_quota(_VendorError("400 INVALID_ARGUMENT: no such voice"))
    assert not speak.is_quota(_VendorError("500 internal"))


def test_the_wait_comes_from_the_service_rather_than_a_guess() -> None:
    """The refusal says how long to wait. Guessing short means being refused again; guessing
    long throws away the rest of the minute."""
    exc = _VendorError("429 RESOURCE_EXHAUSTED ... Please retry in 29.549803963s. ")
    wait = speak._retry_after(exc)

    assert wait is not None and 30 <= wait <= 32, wait


def test_the_wait_is_capped_so_a_bad_number_cannot_hang_the_request() -> None:
    exc = _VendorError("429 RESOURCE_EXHAUSTED 'retryDelay': '86400s'")
    wait = speak._retry_after(exc)

    assert wait is not None and wait <= 65, wait


@pytest.mark.anyio
async def test_more_calls_than_the_allowance_are_held_back_rather_than_sent() -> None:
    """The point of the pacer: the eleventh call in a minute waits for room instead of being
    sent and refused."""
    pace = speak._Pace(rpm=4)

    began = time.monotonic()
    #: Four fit the window and return at once; a fifth cannot, and this asserts it is *not*
    #: sent rather than asserting how long it waits — the wait is a whole minute, which is
    #: not something to sit through in a test.
    await asyncio.gather(*(pace.wait() for _ in range(4)))
    assert time.monotonic() - began < 1, "the calls inside the allowance were delayed"

    held = asyncio.create_task(pace.wait())
    await asyncio.sleep(0.2)
    assert not held.done(), "the call over the allowance was let through"
    held.cancel()


@pytest.mark.anyio
async def test_a_paced_call_that_is_refused_is_retried_and_succeeds() -> None:
    """End to end through the retry: the first attempt is refused, the second is allowed, and
    the caller never sees the refusal."""
    attempts = 0

    async def flaky() -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _VendorError("429 RESOURCE_EXHAUSTED. Please retry in 0.01s")
        return b"\x00\x01"

    got = await speak._with_retry(flaky, speak._Pace(rpm=60))

    assert got == b"\x00\x01"
    assert attempts == 2


@pytest.mark.anyio
async def test_a_failure_that_is_not_quota_is_raised_immediately() -> None:
    attempts = 0

    async def broken() -> bytes:
        nonlocal attempts
        attempts += 1
        raise _VendorError("400 INVALID_ARGUMENT: no such voice")

    with pytest.raises(_VendorError):
        await speak._with_retry(broken, speak._Pace(rpm=60))

    assert attempts == 1, "a permanent failure was retried"


def test_a_transient_server_error_is_told_apart_from_a_quota_refusal() -> None:
    """They mean different things and are handled differently: quota means the allowance is
    spent and the wait is the service's; a 500 means the request never landed and the
    allowance is untouched.

    Seen for real mid-dub. Without this, one 500 on line thirty-six throws away the
    thirty-five lines already synthesized — and, on a metered key, the money they cost.
    """
    wobble = _VendorError("500 INTERNAL. {'error': {'code': 500, 'message': 'An internal error'}}")

    assert not speak.is_quota(wobble), "a server fault is not the creator's quota"
    assert speak._server_wobble(wobble) is not None, "a 500 must be retried"
    assert speak._server_wobble(_VendorError("400 INVALID_ARGUMENT")) is None


@pytest.mark.anyio
async def test_an_empty_answer_is_retried_rather_than_reported_as_a_failure() -> None:
    """Measured against the same model and the same Burmese line, an empty response succeeds
    on the next attempt three times out of three. Giving up on it abandons a line the engine
    was willing to speak."""
    attempts = 0

    async def flaky() -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise speak.EmptyResponseError("gemini-3.1-flash-tts-preview")
        return b"\x00\x01"

    got = await speak._with_retry(flaky, speak._Pace(rpm=60))

    assert got == b"\x00\x01"
    assert attempts == 3


def test_a_model_known_to_be_out_of_quota_is_skipped_until_it_is_worth_asking_again() -> None:
    """Every line in a dub otherwise pays a round trip to a model that has already refused —
    thirty-six wasted requests on a thirty-six line video."""
    speak._SPENT.clear()
    try:
        assert not speak._is_spent("some-model")
        speak._mark_spent("some-model", 30.0)
        assert speak._is_spent("some-model")

        #: And it is never marked spent forever: a per-minute allowance refills, and a model
        #: written off for the life of the process would stay written off after it had.
        speak._mark_spent("other-model", 10_000.0)
        assert speak._SPENT["other-model"] - speak._SPENT["some-model"] < 120
    finally:
        speak._SPENT.clear()
