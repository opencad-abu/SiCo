"""Compatibility facade for the split Virtuoso executor owners.

The facade keeps the historical public and test imports stable while each
owner module holds one responsibility: config binding, snapshot collection,
or shared IPC/artifact/error primitives.
"""

from __future__ import annotations

from .virtuoso_config import run_config_binding as _run_config_binding
from .virtuoso_snapshot import run_snapshot as _run_snapshot
from .virtuoso_support import (
    _blocked,
    _call_snapshot_tools,
    _digest,
    _run_normalizer,
    _write_raw_payloads,
)


def run_config_binding(context):
    return _run_config_binding(context, call_tools=_call_snapshot_tools)


def run_snapshot(context):
    return _run_snapshot(context, call_tools=_call_snapshot_tools)


__all__ = ["run_config_binding", "run_snapshot"]

# Historical private imports used by the LDO adapter and focused tests.
__all__ += ["_blocked", "_call_snapshot_tools", "_digest", "_run_normalizer", "_write_raw_payloads"]
