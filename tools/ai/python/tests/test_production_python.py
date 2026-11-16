from __future__ import annotations

import os
import subprocess
from pathlib import Path
import sys
import sysconfig

import pytest

PRODUCTION_PYTHON = Path(sys.executable)
PRODUCTION_LIB = Path(sysconfig.get_config_var("LIBDIR") or "")
REFERENCE_VERIFIER = (
    Path(__file__).resolve().parents[2]
    / "terminal/scripts/verify_skill_reference_payload.sh"
)


@pytest.mark.skipif(
    not PRODUCTION_PYTHON.is_file() or not PRODUCTION_LIB.is_dir(),
    reason="the production Python library directory is unavailable",
)
def test_production_python_uses_sqlite_or_explicit_json_fallback() -> None:
    python_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    tools_root = Path(__file__).resolve().parents[3]
    environment.update(
        {
            "LD_LIBRARY_PATH": str(PRODUCTION_LIB),
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": os.pathsep.join(
                str(tools_root / relative / "python")
                for relative in ("common", "sico")
            ),
        }
    )
    probe = """
import cadai.cli
from cadai.skill_reference import SkillReference
reference = SkillReference()
assert reference.search({"query": "substring", "limit": 1})["matches"][0]["name"] == "substring"
assert reference.get({"name": "substring"})["matches"][0]["name"] == "substring"
backend = reference.backend
if backend not in {"sqlite", "json-fallback"}:
    raise AssertionError("unexpected skill reference backend: %r" % (backend,))
reference.close()
"""

    result = subprocess.run(
        [str(PRODUCTION_PYTHON), "-c", probe],
        cwd=python_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(
    not PRODUCTION_PYTHON.is_file() or not PRODUCTION_LIB.is_dir(),
    reason="smic28 production Python is unavailable",
)
def test_reference_verifier_requires_the_installation_runtime() -> None:
    source = REFERENCE_VERIFIER.read_text(encoding="utf-8")
    assert 'validation_python=${SICO_HOME-}/runtime/python/bin/python3' in source
    assert "SICO_AI_REFERENCE_VALIDATION_PYTHON" not in source
    assert "/usr/bin/python3" not in source
