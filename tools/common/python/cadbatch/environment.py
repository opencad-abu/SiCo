"""Construct the isolated environment shared by batch task processes."""

from __future__ import annotations

import os
from pathlib import Path

from sicotemp import initialize_project


def task_environment(temp_dir: Path) -> dict[str, str]:
    """Return the environment for a task or its external cancel command.

    The batch launch directory is the single owner of temporary, cache, and
    runtime locations.  Task processes and cancellation commands each take a
    fresh environment snapshot using the same path and vendor EDA rules.
    """

    environment = os.environ.copy()
    initialize_project(environment, temporary=temp_dir)
    return environment


__all__ = ["task_environment"]
