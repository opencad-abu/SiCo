"""Resolve the finite set of cross-tool native process entries."""

import os
from importlib.machinery import EXTENSION_SUFFIXES

from sicopaths import installation

_ENTRIES = {"aiassistant": "ai", "aivw": "aivw"}


def compiled_module(filename):
    return any(filename.endswith(suffix) for suffix in EXTENSION_SUFFIXES)


def native_entry(name, *, environment=None, anchor=None):
    if name not in _ENTRIES:
        raise ValueError("Unknown native tool entry")
    installed = installation(environment, anchor=anchor)
    entry = installed.path("tools/" + _ENTRIES[name] + "/bin/" + name)
    try:
        with entry.open("rb") as stream:
            native = stream.read(4) == b"\x7fELF"
    except OSError:
        native = False
    if not native or not os.access(entry, os.X_OK):
        raise ValueError("Native tool requires an executable ELF " + name + " entry")
    return entry
