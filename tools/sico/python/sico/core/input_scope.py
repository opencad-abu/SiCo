"""Detached task identity and admission values for live user interactions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class InputScope:
    task_id: str
    context: dict
    thread_id: str
    turn_id: str
    origin: str
    access: dict
    executing: bool
    admitted: bool
