"""Source ownership and loader ordering for shared GUI and LSF modules."""

import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest
from skill_style import analyze
from skill_syntax import parse as _parse
from skill_test_support import ROOT, common_source_loads, skill_group_paths


@pytest.mark.parametrize("group,suffix", [("gui", ".ils"), ("lsf", ".il")])
def test_shared_inventory_preserves_source_language_and_all_frontend_orders(group, suffix):
    sources = skill_group_paths("common", group)
    names = [p.name for p in sources]
    assert len(names) == len(set(names))
    definitions = []
    for path in sources:
        source = path.read_text()
        assert path.suffix == suffix
        assert len(source.splitlines()) < 300
        assert analyze(path) == []
        _, frames = _parse(source)
        assert all(f.close_token for f in frames)
        definitions += re.findall(r"^(?:procedure|defclass|defmethod|defmacro|defgeneric)\((\w+)", source, re.M)
    assert len(definitions) == len(set(definitions))
    for flow in ("rce", "drc", "lvs", "lef"):
        source = (ROOT / flow / "skill++" / f"{flow.upper()}.ils").read_text()
        offsets = [source.index(f'"/{path.parent.name}/{path.name}"') for path in sources]
        assert offsets == sorted(offsets)
        if group == "gui":
            revision = "cadCommonGuiModulesRevision"
            revision_value = "20260924.common.environment.v3"
        else:
            revision = "SICO_lsfModulesRevision"
            revision_value = "20260924.flow.environment.v3"
        assert source.count(f'{revision}()=="{revision_value}"') >= 2
        if group == "lsf":
            assert source.index('"/skill/SICO_guiProtocol.il"') < offsets[0]
            assert offsets[-1] < source.index('"/skill/SICO_lsfMonitor.il"')


def test_standalone_lsf_menu_loads_explicit_discovery_group():
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    menu = tomllib.loads((ROOT / "utility/menu.toml").read_text())
    # Inspect the source callbacks without depending on the menu tree schema.
    callbacks = re.findall(r'^callback = (".*")$', (ROOT / "utility/menu.toml").read_text(), re.M)
    callbacks = [json.loads(value) for value in callbacks if "SICO_lsfMonitorStart" in value]
    assert len(callbacks) == 3
    assert menu
    for callback in callbacks:
        offsets = [callback.index(str(path.relative_to(ROOT))) for path in skill_group_paths("common", "lsf")]
        assert offsets == sorted(offsets)
        assert offsets[-1] < callback.index("common/skill/SICO_lsfMonitor.il")


@pytest.mark.skipif(os.environ.get("RCE_RUN_SKILL_PROBE") != "1", reason="set RCE_RUN_SKILL_PROBE=1")
def test_lsf_source_reload_keeps_inflight_state_and_exit_cleanup_guard_with_dbaccess():
    executable = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if executable is None:
        pytest.skip("dbAccess unavailable")
    source = f'''
{common_source_loads("lsf")}
hosts=cadLsfDiscoveryState
queues=cadLsfQueueDiscoveryState
hosts[42]='activeHost
queues[43]='activeQueue
{common_source_loads("lsf")}
SICO_lsfInitializeRuntime()
when(and(eq(hosts cadLsfDiscoveryState) eq(queues cadLsfQueueDiscoveryState)
         cadLsfDiscoveryState[42]=='activeHost
         cadLsfQueueDiscoveryState[43]=='activeQueue cadLsfExitCleanupRegistered)
  printf("CAD_LSF_RELOAD_STATE_OK\\n"))
remove(42 cadLsfDiscoveryState)
remove(43 cadLsfQueueDiscoveryState)
exit()
'''
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        completed = subprocess.run([executable], input=source, text=True,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   timeout=30, check=False)
        log.seek(0)
        output = log.read()
    assert completed.returncode == 0, output
    assert "*Error*" not in output, output
    assert "CAD_LSF_RELOAD_STATE_OK" in output, output
