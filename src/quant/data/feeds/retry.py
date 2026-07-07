"""Bounded retry with exponential backoff for flaky data-source calls.

A single network hiccup should not permanently fail a scheduled live run. A
transient error (timeout, connection reset, upstream 5xx) is retried a few
times with growing backoff; a data-contract error (e.g. "no data for symbol",
"unsupported timeframe") is *fatal* and re-raised immediately, since retrying a
request that can never succeed only wastes the scheduling window.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from quant.utils import get_logger

log = get_logger(__name__)

T = TypeVar("T")


def with_retries(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    fatal: tuple[type[BaseException], ...] = (ValueError,),
    label: str = "call",
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `fn`, retrying transient failures up to `attempts` times.

    Backoff between tries is exponential: base_delay, 2x, 4x, ... capped at
    `max_delay`. Exceptions in `fatal` are non-transient and re-raised at once
    (no retry). Once attempts are exhausted the last transient error is raised,
    so callers upstream (e.g. the freshness gate) still fail safe.

    `sleep` is injectable so tests run without real delays.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    last: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except fatal:
            raise
        except Exception as exc:  # noqa: BLE001 - transient data-source failure: back off, then re-raise
            last = exc
            if i >= attempts:
                break
            delay = min(base_delay * 2 ** (i - 1), max_delay)
            log.warning(f"{label} failed (attempt {i}/{attempts}): {exc!r} - retrying in {delay:.1f}s")
            sleep(delay)

    assert last is not None  # loop body sets `last` before it can break
    log.error(f"{label} failed after {attempts} attempts: {last!r}")
    raise last
