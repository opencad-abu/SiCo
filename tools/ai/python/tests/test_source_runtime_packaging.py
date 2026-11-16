"""Retired source release entry points reject all deployment attempts."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("script,args", [
    ("../deploy/package_mts_netlistor_release.sh", []),
    ("ai/terminal/scripts/package_source_runtime_release.sh", ["missing.tar.gz"]),
    ("ai/terminal/scripts/package_release.sh", ["rhel7", "missing/ai"]),
    ("ai/terminal/scripts/package_rhel7_release.sh", ["missing/ai"]),
    ("ai/terminal/scripts/package_rhel8_release.sh", ["missing/ai"]),
    ("ai/terminal/scripts/package_universal_release.sh", ["missing/ai", "missing/ai"]),
])
def test_legacy_packagers_fail_closed_before_writing(tmp_path, script, args):
    archive = tmp_path / "release.tar.gz"
    result = subprocess.run(
        [str(ROOT / script), *args, str(archive)], cwd=tmp_path,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "runtime-only policy" in result.stderr
    assert "package_runtime_release.py" in result.stderr
    assert not list(tmp_path.iterdir())
