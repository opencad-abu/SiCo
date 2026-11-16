"""Shared generics retain every member's methods across loader upgrades."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

import utility.skill_loader as skill_loader


ROOT = Path(__file__).resolve().parents[3]
DB = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess")


def installation(tmp_path):
    root = tmp_path / "installation"
    for directory in ("bin", "tools", "tools/common", "etc/config", "scripts"):
        (root / directory).mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/sicoSkillLoader.ils",
                 root / "scripts/sicoSkillLoader.ils")
    (root / "etc/config/sico-install.json").write_text(
        '{"format":"cad.runtime.install.v1","product":"Silicon Copilot","layout":1}\n',
        encoding="ascii")
    source_dir = root / "tools/common/skill++"
    source_dir.mkdir()
    (source_dir / "first.ils").write_text(
        'defclass(SICOTestFirst () ())\n'
        'defmethod(sicoSharedBuild ((inst SICOTestFirst)) "first")\n',
        encoding="ascii")
    (source_dir / "second.ils").write_text(
        'defclass(SICOTestSecond () ())\n'
        'defmethod(sicoSharedBuild ((inst SICOTestSecond)) "second")\n',
        encoding="ascii")
    groups = {"shared": {
        "output": "loader.ils",
        "requires": [],
        "sources": ["tools/common/skill++/first.ils",
                    "tools/common/skill++/second.ils"],
    }}
    (root / "loader.ils").write_text(
        skill_loader.render(root, "shared", groups), encoding="ascii")
    return root


@pytest.mark.skipif(not DB.is_file(), reason="IC231 dbAccess is not installed")
def test_shared_generic_keeps_methods_from_every_member(tmp_path):
    root = installation(tmp_path)
    loader = json.dumps(str(root / "loader.ils"))
    source = f'''
procedure(SICO_installationRoot(@optional supplied) {json.dumps(str(root))})
load({loader})
first=errset(sicoSharedBuild(makeInstance('SICOTestFirst)) t)
second=errset(sicoSharedBuild(makeInstance('SICOTestSecond)) t)
unless(and(equal(first '("first")) equal(second '("second")))
  error("Shared generic lost a member method"))
load({loader})
unless(and(equal(sicoSharedBuild(makeInstance('SICOTestFirst)) "first")
           equal(sicoSharedBuild(makeInstance('SICOTestSecond)) "second"))
  error("Repeated load changed shared methods"))
printf("SHARED_METHODS_PASS\\n")
exit()
'''
    probe = tmp_path / "probe.il"
    probe.write_text(source, encoding="ascii")
    env = {key: value for key, value in os.environ.items()
           if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "CAD_HOME"}
           and not key.startswith("SICO_")}
    env["SICO_HOME"] = str(root)
    result = subprocess.run(
        [str(DB), "-load", str(probe)], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=60, check=False)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "SHARED_METHODS_PASS" in output, output
    assert "no applicable method" not in output, output
    assert "redefined" not in output.lower(), output
