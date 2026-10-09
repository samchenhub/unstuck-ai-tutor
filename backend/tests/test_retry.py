import pytest

from unstuck.llm.retry import with_retry


def test_returns_first_success():
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    assert with_retry(fn, sleep=lambda s: None) == "ok"
    assert len(calls) == 1


def test_retries_with_exponential_backoff():
    outcomes = iter([ValueError(), ValueError(), "ok"])
    delays = []

    def fn():
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value

    assert with_retry(fn, attempts=4, base_delay=1.0, sleep=delays.append) == "ok"
    assert delays == [1.0, 2.0]


def test_gives_up_after_attempts():
    delays = []

    def fn():
        raise ValueError("always")

    with pytest.raises(ValueError):
        with_retry(fn, attempts=3, base_delay=0.5, sleep=delays.append)
    assert delays == [0.5, 1.0]


def test_does_not_retry_unlisted_errors():
    delays = []

    def fn():
        raise KeyError("nope")

    with pytest.raises(KeyError):
        with_retry(fn, retry_on=(ValueError,), sleep=delays.append)
    assert delays == []
