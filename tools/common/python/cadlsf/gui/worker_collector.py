"""Cancellation-aware collector construction for GUI worker tasks."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from ..collector import CollectorConfig, LsfCollector, SubprocessRunner

CollectorFactory = Callable[[CollectorConfig, Event], LsfCollector]

def create_collector(config: CollectorConfig, cancel_event: Event) -> LsfCollector:
    return LsfCollector(
        config=config,
        runner=SubprocessRunner(cancelled=cancel_event.is_set),
    )
