"""Guidance owners and real runtime assembly cannot import service implementations."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULES = {row["id"]: row for row in load_manifest()["rules"]}


@pytest.mark.parametrize("name", [
    "core-workbench-instructions-runtime-free",
    "codex-audit-instructions-runtime-free",
    "codex-runtime-assembly-no-service",
    "core-loop-no-service-runtime",
])
def test_sources_obey_runtime_guidance_boundaries(name):
    assert not check_rule(RULES[name], root=ROOT)


@pytest.mark.parametrize("rule,module", [
    ("core-workbench-instructions-runtime-free", "sico.codex"),
    ("core-workbench-instructions-runtime-free", "sico.storage"),
    ("codex-audit-instructions-runtime-free", "sico.service.audit"),
    ("codex-audit-instructions-runtime-free", "subprocess"),
    ("codex-runtime-assembly-no-service", "sico.service.audit"),
    ("core-loop-no-service-runtime", "sico.service.workbench"),
])
def test_guidance_rules_reject_deferred_imports(rule, module):
    assert violations("def run():\n    import " + module, RULES[rule],
                      module="sico.core.workbench_instructions")


@pytest.mark.parametrize("mode", ["python", "start", "resume", "failure"])
def test_real_guidance_consumers_without_external_io(tmp_path, mode):
    probe = Path(__file__).with_name("runtime_guidance_probe.py")
    result = subprocess.run(
        [sys.executable, "-B", "-s", str(probe), str(ROOT), mode],
        cwd=tmp_path, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
