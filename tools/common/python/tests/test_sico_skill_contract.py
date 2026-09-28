"""Exercise the shared installation contract in the licensed SKILL interpreter."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from sicopaths import IDENTITY_BYTES, MARKER


ROOT = Path(__file__).resolve().parents[4]


def test_skill_installation_and_environment_match_python(tmp_path):
    executable = shutil.which("dbAccess")
    if executable is None:
        pytest.skip("dbAccess is unavailable")
    root = tmp_path / "prefix with spaces"
    (root / MARKER).parent.mkdir(parents=True)
    (root / MARKER).write_bytes(IDENTITY_BYTES)
    (root / "bin").mkdir()
    (root / "tools/common").mkdir(parents=True)
    alias = tmp_path / "current"
    alias.symlink_to(root)
    code = f'''
load("{ROOT}/tools/common/skill/SICO_environment.il")
load("{ROOT}/tools/common/skill/SICO_installation.il")
unless(SICO_installationRoot("{root}")=="{root}" error("root mismatch"))
unless(SICO_installationRoot("{root}/tools/common/../..")=="{root}"
       error("trailing directory separator mismatch"))
unless(SICO_iconPath("{root}" "brand" "logo.png")==
       "{root}/share/sico/icons/brand/logo.png" error("icon mismatch"))
when(errset(SICO_installationRoot("{tmp_path}") nil) error("mixed roots accepted"))
when(errset(SICO_installationPath("{root}" "../escape") nil) error("escape accepted"))
setShellEnvVar("SICO_HOME" "")
when(errset(SICO_installationRoot("{root}") nil) error("empty root accepted"))
unless(SICO_environmentValue("SICO_API_KEY" list("CAD_AGENT_API_KEY"))==""
       error("empty canonical value lost"))
printf("SICO_CONTRACT_OK\\n")
exit()
'''
    env = dict(os.environ, SICO_HOME=str(alias), CAD_HOME=str(root),
               SICO_API_KEY="", CAD_AGENT_API_KEY="private-test-secret")
    result = subprocess.run([executable], input=code, text=True, capture_output=True,
                            cwd=tmp_path, env=env, timeout=40)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "SICO_CONTRACT_OK" in output
    assert "*Error*" not in output
    assert "private-test-secret" not in output
    assert "deprecated; use" not in output
