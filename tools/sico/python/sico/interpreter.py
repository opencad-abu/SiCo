"""CAD interpreter selection without importing the existing CLI runtime."""

from __future__ import annotations

import os
import sys
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path

from . import PRODUCT_NAME
from .installation import current


def cad_python(environment=None):
    """Return the only interpreter owned by this SiCo installation."""
    env = os.environ if environment is None else environment
    selected = current(env)
    python = selected.runtime_python
    if python is None:
        raise ValueError("$SICO_HOME/runtime/python/bin/python3 is unavailable")
    return str(python)


def agent_command(*arguments, environment=None):
    entry = native_entry("sico")
    if entry is not None and _is_native_binary(entry):
        # A packaged launcher is an executable and selects its runtime itself.
        return [str(entry), *arguments]
    if any(__file__.endswith(suffix) for suffix in EXTENSION_SUFFIXES):
        raise ValueError("Native SiCo runtime requires an executable ELF sico relay")
    source_entry = current().tool("sico") / "python/sico-entry"
    if not source_entry.is_file():
        raise ValueError("SiCo development entry is unavailable")
    return [cad_python(environment), "-s", str(source_entry), *arguments]


def _is_native_binary(path):
    try:
        with open(path, "rb") as stream:
            return stream.read(4) == b"\x7fELF"
    except OSError:
        return False


def native_entry(name: str) -> Path | None:
    """Locate only a declared native worker in the validated main tool."""
    if name not in {"sico", "sico-mcp"}:
        raise ValueError("Unknown SiCo native entry")
    candidate = current().path("tools/sico/bin/" + name)
    if candidate.is_file() and os.access(candidate, os.X_OK) and _is_native_binary(candidate):
        return candidate
    return None


def ensure_cad_python(entry):
    selected = cad_python()
    if os.path.realpath(selected) != os.path.realpath(sys.executable):
        env = dict(os.environ)
        for key in ("PYTHONHOME", "PYTHONPATH", "LD_PRELOAD", "LD_AUDIT"):
            env.pop(key, None)
        os.execve(selected, [selected, "-s", str(entry), *sys.argv[1:]], env)
    if sys.version_info < (3, 9):  # noqa: UP036 -- launcher can be invoked by a site Python
        raise RuntimeError(PRODUCT_NAME + " requires Python 3.9 or newer")
