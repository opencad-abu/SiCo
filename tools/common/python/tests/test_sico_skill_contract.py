"""Exercise the shared installation contract in the licensed SKILL interpreter."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from sicopaths import IDENTITY_BYTES, MARKER


ROOT = Path(__file__).resolve().parents[4]


def test_skill_default_logo_is_silicon_copilot():
    source = (ROOT / "tools/common/skill/SICO_toml.il").read_text(encoding="utf-8")
    assert 'or(logo company "Silicon Copilot")' in source


def test_skill_python_selectors_prefer_the_installed_runtime():
    selectors = (
        "tools/common/skill/SICO_state.il",
        "tools/common/skill/SICO_profile.il",
        "tools/common/skill/SICO_lsfCommands.il",
        "tools/common/skill/SICO_batchCore.il",
        "tools/drc/skill++/DRCRUN.ils",
        "tools/lvs/skill++/LVSRUN.ils",
        "tools/lef/skill++/LEFRUN.ils",
        "tools/rce/skill/RCE_toml.il",
        "tools/ai/skill/AI_launcher.il",
        "tools/ai/skill/AI_flow_command.il",
        "tools/sico/skill/SICO_launcher.il",
        "tools/utility/menu.il",
    )

    for relative in selectors:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "SICO_installedPython" in text, relative
        if "SICO_PYTHON" in text:
            assert text.index("SICO_installedPython") < text.index("SICO_PYTHON"), relative


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


def test_skill_installed_python_requires_the_installation_runtime(tmp_path):
    executable = shutil.which("dbAccess")
    if executable is None:
        pytest.skip("dbAccess is unavailable")

    def installation(name):
        root = tmp_path / name
        (root / MARKER).parent.mkdir(parents=True)
        (root / MARKER).write_bytes(IDENTITY_BYTES)
        (root / "bin").mkdir()
        (root / "tools/common").mkdir(parents=True)
        return root

    bundled_root = installation("bundled")
    bundled = bundled_root / "runtime/python/bin/python3"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("", encoding="ascii")
    bundled.chmod(0o755)

    missing_root = installation("missing")
    dev = tmp_path / "site-python/bin/python3"
    dev.parent.mkdir(parents=True)
    dev.write_text("", encoding="ascii")
    dev.chmod(0o755)
    (missing_root / "etc/config/dev-runtime").write_text(str(dev) + "\n", encoding="ascii")

    code = f'''
load("{ROOT}/tools/common/skill/SICO_environment.il")
load("{ROOT}/tools/common/skill/SICO_installation.il")
unless(SICO_installedPython("{bundled_root}")=="{bundled}"
       error("bundled Python mismatch"))
unless(!SICO_installedPython("{missing_root}")
       error("missing runtime must not fall back"))
printf("SICO_INSTALLED_PYTHON_OK\\n")
exit()
'''
    result = subprocess.run(
        [executable],
        input=code,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=dict(os.environ, SICO_HOME=str(bundled_root)),
        timeout=40,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "SICO_INSTALLED_PYTHON_OK" in output
    assert "*Error*" not in output
