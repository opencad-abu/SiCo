"""Remove frontend loader overrides before starting the interactive shell."""

from __future__ import annotations

import os
from typing import MutableMapping


# Loader variables belong to the frontend process, not to the interactive
# shell.  Inheriting them shadows the system libraries for ordinary commands
# (for example the site's ``register-python-argcomplete``), so they are
# dropped from the shell environment like a normal site login shell.
SHELL_ENV_DROP = frozenset(
    {
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "LD_AUDIT",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONNOUSERSITE",
    }
)


def sanitize_shell_environment(
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Drop the frontend's loader variables from the shell environment.

    QTermWidget starts the shell with this process environment, and
    ``setEnvironment`` only merges entries, so the variables must be removed
    before the shell starts.  The frontend has already loaded Qt and its
    QTermWidget runtime at this point.
    """

    target = os.environ if environ is None else environ
    for name in SHELL_ENV_DROP:
        target.pop(name, None)
