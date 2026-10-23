"""Persistence capability needed by the model loop, independent of storage owners."""

from __future__ import annotations

from typing import Protocol

from .contracts import RunState


class RunJournal(Protocol):
    @property
    def state(self) -> RunState | None: ...

    def append(self, kind: str, payload: dict, state: RunState) -> dict: ...

    def artifact(self, content: bytes) -> dict: ...
