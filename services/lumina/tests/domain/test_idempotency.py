from __future__ import annotations

from lumina.domain.jobs import Stage, idempotency_key


def test_same_inputs_produce_the_same_key() -> None:
    a = idempotency_key(Stage.GENERATE, {"scene": "s1", "seed": 7})
    b = idempotency_key(Stage.GENERATE, {"scene": "s1", "seed": 7})
    assert a == b


def test_key_is_stable_across_dict_ordering() -> None:
    """Load-bearing. An unsorted dict yields a different digest for identical work, which
    re-runs and re-charges the stage."""
    a = idempotency_key(Stage.GENERATE, {"scene": "s1", "seed": 7, "tier": "image_motion"})
    b = idempotency_key(Stage.GENERATE, {"tier": "image_motion", "seed": 7, "scene": "s1"})
    assert a == b


def test_different_stages_never_collide() -> None:
    payload = {"scene": "s1"}
    assert idempotency_key(Stage.GENERATE, payload) != idempotency_key(Stage.VOICE, payload)


def test_different_inputs_produce_different_keys() -> None:
    assert idempotency_key(Stage.GENERATE, {"seed": 1}) != idempotency_key(
        Stage.GENERATE, {"seed": 2}
    )
