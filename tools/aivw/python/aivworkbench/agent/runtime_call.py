"""Bounded execution helpers for runtime external boundaries."""

from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

_CallResult = TypeVar("_CallResult")

class _BoundedCallTimeout(TimeoutError):
    """Internal marker for a worker that did not finish before its deadline."""


def _call_with_timeout(
    function: Callable[[], _CallResult],
    timeout_seconds: float,
    *,
    name: str,
) -> _CallResult:
    """Run one blocking boundary with a real wall-clock upper bound.

    Python cannot safely terminate an arbitrary thread.  The worker is
    therefore a daemon and a timed-out mutating call is handled by the caller
    as ``UNKNOWN_SIDE_EFFECT``.  A timed-out provider call is side-effect free
    from AIVW's perspective and becomes ``PROVIDER_TIMEOUT``.  In both cases
    the late result is discarded and the runtime never retries it implicitly.
    """

    completed = threading.Event()
    outcome: dict[str, Any] = {}

    def invoke() -> None:
        try:
            outcome["value"] = function()
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            completed.set()

    worker = threading.Thread(
        target=invoke,
        name="aivw-%s" % name,
        daemon=True,
    )
    worker.start()
    if not completed.wait(timeout_seconds):
        raise _BoundedCallTimeout("%s exceeded %.6g seconds" % (name, timeout_seconds))
    error = outcome.get("error")
    if isinstance(error, BaseException):
        raise error
    return outcome["value"]


def call_with_timeout(function: Callable[[], _CallResult], timeout_seconds: float, *, name: str) -> _CallResult:
    return _call_with_timeout(function, timeout_seconds, name=name)

__all__ = ["_BoundedCallTimeout", "call_with_timeout"]
