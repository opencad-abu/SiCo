"""Installation paths used by the main application and its child processes."""

import os
from pathlib import Path

from sicopaths import installation


def current(environment=None):
    return installation(environment, anchor=Path(__file__).resolve().parents[4])


def python_path():
    selected = current()
    return os.pathsep.join(str(selected.tool(tool) / "python")
                           for tool in ("sico", "ai", "common"))
