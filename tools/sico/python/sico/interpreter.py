"""CAD interpreter selection without importing the existing CLI runtime."""

from __future__ import annotations

import os
import sys
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path

from sicoenv import value

from . import PRODUCT_NAME
from .installation import current


def cad_python(environment=None):
    env = os.environ if environment is None else environment
    explicit = value(env, "SICO_PYTHON", ("CAD_PYTHON",))
    root = value(env, "SICO_PYTHON_ROOT", ("CAD_PYTHON_ROOT",))
    if ("SICO_PYTHON" in env and not explicit) or ("SICO_PYTHON_ROOT" in env and not root):
        raise ValueError("Explicit SICO_PYTHON/SICO_PYTHON_ROOT must not be empty")
    selected = current(env)
    bundled = selected.runtime_python
    if bundled is not None:
        # One installation owns one interpreter. Stale modulefile selectors
        # must not make the source launcher disagree with the native launcher.
        return str(bundled)
    dev = selected.dev_python
    if explicit:
        path = Path(explicit).expanduser()
    elif root:
        path = Path(root).expanduser() / "bin/python3"
    elif dev is not None:
        path = dev
    else:
        path = Path("/software/pkgs/python/3.9.13/bin/python3")
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(
            "SiCo Python is unavailable: set $SICO_HOME/runtime/python or "
            "etc/config/dev-runtime"
        )
    return str(path)  # Preserve site NAS aliases.


def agent_command(*arguments):
    entry = native_entry("sico")
    if entry is not None and _is_native_binary(entry):
        # A packaged launcher is an executable; it selects CAD_PYTHON itself and
        # must never be handed to Python as a script.
        return [str(entry), *arguments]
    if any(__file__.endswith(suffix) for suffix in EXTENSION_SUFFIXES):
        raise ValueError("Native SiCo runtime requires an executable ELF sico relay")
    source_entry = current().tool("sico") / "python/sico-entry"
    if not source_entry.is_file():
        raise ValueError("SiCo development entry is unavailable")
    return [cad_python(), "-s", str(source_entry), *arguments]


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
