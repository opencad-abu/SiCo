"""Display publication values shared by local, remote and UI consumers."""

from dataclasses import dataclass

DISPLAY_CONTRACT = "agent_display.v1"


@dataclass(frozen=True)
class DisplayBatch:
    identity: tuple
    activation: int
    start: int
    end: int
    committed: int
    events: tuple
    snapshot: object = None
    size: int = 0
    contract: str = DISPLAY_CONTRACT
    error: str = ""

