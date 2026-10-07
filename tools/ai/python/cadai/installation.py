"""Derive the Assistant runtime from the single validated SiCo installation."""

from pathlib import Path

from sicoenv import read
from sicopaths import installation


def current(environment=None):
    return installation(environment, anchor=Path(__file__).resolve().parents[4])


def runtime_root(environment, candidates=()):
    """Reject old runtime roots that would combine different installations."""
    selected = current(environment).tool("ai")
    legacy = read(environment, "SICO_AI_RELEASE_ROOT")
    for candidate in (*candidates, *((legacy,) if legacy is not None else ())):
        path = Path(candidate).expanduser()
        if not str(candidate).strip() or not path.is_absolute() or path.resolve() != selected:
            raise ValueError("Assistant runtime root conflicts with SICO_HOME/tools/ai")
    return selected
