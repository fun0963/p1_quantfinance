"""Tests for the bounded-retry helper that hardens data-source calls.

A single network hiccup must not permanently fail a scheduled run; a real
data-contract error must fail fast without wasting retries. `sleep` is injected
so these run instantly.
"""
from __future__ import annotations

import pytest

from quant.data.feeds.retry import with_retries


def test_returns_first_success_without_sleeping():
    calls = {"n": 0}
    slept: list[float] = []

    def ok():
        calls["n"] += 1
        return "value"

    out = with_retries(ok, sleep=slept.append)
    assert out == "value"
    assert calls["n"] == 1
    assert slept == []  # never backed off


def test_retries_transient_then_succeeds():
    attempts = {"n": 0}
    slept: list[float] = []

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("transient")
        return "recovered"

    out = with_retries(flaky, base_delay=1.0, sleep=slept.append)
    assert out == "recovered"
    assert attempts["n"] == 3
    assert slept == [1.0, 2.0]  # exponential backoff between the 3 tries


def test_reraises_last_error_after_exhausting_attempts():
    attempts = {"n": 0}

    def always_fails():
        attempts["n"] += 1
        raise TimeoutError(f"boom {attempts['n']}")

    with pytest.raises(TimeoutError, match="boom 3"):
        with_retries(always_fails, attempts=3, sleep=lambda _: None)
    assert attempts["n"] == 3


def test_fatal_errors_are_not_retried():
    attempts = {"n": 0}

    def no_data():
        attempts["n"] += 1
        raise ValueError("yfinance returned no data")

    with pytest.raises(ValueError, match="no data"):
        with_retries(no_data, sleep=lambda _: None)
    assert attempts["n"] == 1  # fatal -> tried exactly once


def test_backoff_is_capped_at_max_delay():
    slept: list[float] = []
    attempts = {"n": 0}

    def always_fails():
        attempts["n"] += 1
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        with_retries(always_fails, attempts=5, base_delay=10.0, max_delay=15.0, sleep=slept.append)
    # delays would be 10, 20, 40, 80 -> capped to 10, 15, 15, 15
    assert slept == [10.0, 15.0, 15.0, 15.0]


def test_invalid_attempts_rejected():
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        with_retries(lambda: None, attempts=0)
