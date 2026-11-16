# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""Session-routing final acceptance on the two authorized real project entries.

Starts a fresh IC23.1 Virtuoso with the real project ``cds.lib``, drives the
product startup chain (``SICO.ils`` -> ``sicoShow`` -> one shared
instance bridge + two Qt desktops) and exercises FIFO admission, controlled
timeout, late-reply quarantine, desktop close isolation and generation
retirement.  The controlled timeout is a pure SKILL sleep; no design object is
created, modified or saved and every runtime byte stays in the private launch
directory below ``tmp_path``.

Opt in with ``CAD_AI_RUN_ROUTER_TESTS=1`` and a JSON ``CAD_AI_ROUTER_PROJECTS``
list of ``{label, cdslib, project_home, lib, fingerprint, graphical}`` entries.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from router_project_config import (
    CAD,
    CAD_PYTHON,
    FIFO_SLEEP,
    LATE_SLEEP,
    SKILL_TIMEOUT,
    VIRTUOSO,
)
from router_project_files import _digest_of as _digest_of

# Compatibility helper exports retain the original licensed collector.
from router_project_files import _file_digest as _file_digest
from router_project_files import _read_fields as _read_fields
from router_project_files import _receipt_core as _receipt_core
from router_project_files import _tree_digest as _tree_digest
from router_project_processes import _ancestry as _ancestry
from router_project_processes import _progress_log as _progress_log
from router_project_processes import _scan_processes as _scan_processes
from router_project_processes import _wait as _wait
from router_project_startup import _quote as _quote
from router_project_startup import _startup_source as _startup_source

from sico.core.contracts import BoundContext, NeedsReconcile
from sico.transport.bridge_client import BridgeClient
from sico.transport.broker import skill_call_context
from sico.transport.router_archive import maintain_router_logs


def _project_cases():
    raw = os.environ.get("CAD_AI_ROUTER_PROJECTS", "").strip()
    if not raw:
        return []
    cases = json.loads(raw)
    assert isinstance(cases, list) and cases
    for case in cases:
        if not isinstance(case, dict) or not {
            "label", "cdslib", "project_home", "lib", "fingerprint", "graphical"
        } <= set(case):
            raise ValueError("Invalid CAD_AI_ROUTER_PROJECTS entry")
    return cases


CASES = _project_cases()
pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_ROUTER_TESTS") != "1" or not VIRTUOSO.is_file() or not CASES,
    reason=(
        "licensed session-routing acceptance requires CAD_AI_RUN_ROUTER_TESTS=1 "
        "and CAD_AI_ROUTER_PROJECTS"
    ),
)


from router_project_scenario import _run_project as _run_project


@pytest.mark.parametrize("project", CASES, ids=[case["label"] for case in CASES])
def test_real_project_session_routing(project, tmp_path):
    fingerprint_before = _tree_digest(project["fingerprint"])
    _run_project(project, tmp_path, "acceptance")
    fingerprint_after = _tree_digest(project["fingerprint"])
    changed = sorted(
        path for path in set(fingerprint_before) | set(fingerprint_after)
        if fingerprint_before.get(path) != fingerprint_after.get(path)
    )
    assert not changed, changed[:20]
    (tmp_path / "acceptance" / "fingerprint.json").write_text(json.dumps({
        "roots": [str(path) for path in project["fingerprint"]],
        "files": len(fingerprint_before),
        "digest": _digest_of(fingerprint_before),
        "changed": changed,
    }, indent=2))
