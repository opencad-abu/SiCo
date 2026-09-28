"""Local replay publication shared by clients; never a wire payload."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CursorPosition:
    sequence: int


@dataclass(frozen=True)
class ReplayView:
    stream: object
    committed_sequence: int
    context: object = None
