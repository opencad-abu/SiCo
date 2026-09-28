"""Check renamed flow settings in the licensed interpreter and LSF boundary."""

import json
import os
from pathlib import Path

import pytest

from cadlsf.cli import main
from skill_probe_support import run_virtuoso_source

ROOT = Path(__file__).resolve().parents[4]
RENAMES = {
    **{"PROJ_" + name: name for name in (
        "DRC_DB_DIR", "LVS_DB_DIR", "RCE_DB_DIR", "GDS_DB_DIR", "CDL_DB_DIR",
        "LEF_DB_DIR", "DRC_CONFIG", "LVS_CONFIG", "RCE_CONFIG", "LEF_CONFIG",
        "DRC_FILE", "LVS_FILE", "RCE_LVS_FILE", "QUANTUS_TECH_DIR",
        "STARRC_TECH_DIR", "CALXRC_TECH_DIR", "HCELL_FILE", "LEFGEN_OPT_FILE",
        "LEF_CELL_LIST",
    )},
    **{"EXT_" + name: "RCE_" + name for name in (
        "DEF_TOOL", "CORNER", "DEF_TEMP", "TEMP_LIST", "OUTPUT_CHOICES",
    )},
    "EXT_CPU_ALLOW": "SICO_FLOW_CPU_ALLOW",
    **{"EXT_LSF_" + name: "SICO_LSF_" + name for name in ("BSUB", "BKILL", "BQUEUES")},
}
LICENSED = pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1", reason="set RCE_RUN_SKILL_PROBE=1",
)


@LICENSED
@pytest.mark.parametrize("mode", ["absent", "old", "new", "both", "empty"])
def test_flow_environment_names_have_one_authority(tmp_path, monkeypatch, mode):
    code = [f'load({json.dumps(str(ROOT / "tools/common/skill/SICO_environment.il"))})']
    for old, new in RENAMES.items():
        monkeypatch.delenv(old, raising=False)
        monkeypatch.delenv(new, raising=False)
        if mode in ("old", "both", "empty"):
            monkeypatch.setenv(old, "private-old-value")
        if mode in ("new", "both", "empty"):
            monkeypatch.setenv(new, "" if mode == "empty" else "current-value")
        if mode == "old":
            check = f'null(errset(SICO_envValue("{new}") nil))'
        elif mode in ("empty", "absent"):
            check = f'null(SICO_envValue("{new}"))'
        else:
            check = f'SICO_envValue("{new}")=="current-value"'
        code.append(f'unless({check} error("flow environment contract: {new}"))')
    code.extend(['printf("FLOW_ENV_PASS\\n")', 'exit()'])
    output = run_virtuoso_source("\n".join(code), tmp_path, log_path=tmp_path / "probe.log")
    assert "FLOW_ENV_PASS" in output
    assert "private-old-value" not in output


@LICENSED
def test_real_flow_frontends_consume_renamed_settings(tmp_path, monkeypatch):
    for old, new in RENAMES.items():
        monkeypatch.delenv(old, raising=False)
        monkeypatch.delenv(new, raising=False)
    env = {"SICO_HOME": str(ROOT), "SICO_PYTHON": "/software/pkgs/python/3.9.13/bin/python3",
           "SICO_FLOW_CPU_ALLOW": "1,3,6", "RCE_DEF_TOOL": "QRC", "RCE_DEF_TEMP": "37",
           "RCE_TEMP_LIST": "37,85", "RCE_OUTPUT_CHOICES": "dspf,view", "RCE_CORNER": "Probe"}
    for flow in ("DRC", "LVS", "RCE", "GDS", "CDL", "LEF"):
        env[flow + "_DB_DIR"] = str(tmp_path / flow)
        if flow in ("DRC", "LVS", "RCE", "LEF"):
            env[flow + "_CONFIG"] = str(tmp_path / (flow + "-profile.toml"))
    for name in ("DRC_FILE", "LVS_FILE", "RCE_LVS_FILE"):
        env[name] = "probe," + str(tmp_path / name)
    for name in ("QUANTUS_TECH_DIR", "STARRC_TECH_DIR", "CALXRC_TECH_DIR"):
        env[name] = "probe," + str(tmp_path / name)
    for name in ("HCELL_FILE", "LEFGEN_OPT_FILE", "LEF_CELL_LIST"):
        env[name] = str(tmp_path / name)
    code = [f'load("{ROOT}/tools/{flow}/skill++/{flow.upper()}.ils")'
            for flow in ("drc", "lvs", "rce", "lef")]
    code += [
        'unless(drcStruc->defOutDir==getShellEnvVar("DRC_DB_DIR") error("DRC root"))',
        'unless(lvsStruc->defOutDir==getShellEnvVar("LVS_DB_DIR") error("LVS root"))',
        'unless(extstrStruc->defOutDir==getShellEnvVar("RCE_DB_DIR") error("RCE root"))',
        'unless(lefStruc->defOutDir==getShellEnvVar("LEF_DB_DIR") error("LEF root"))',
        'unless(lvsStageProjectDir("gds")==getShellEnvVar("GDS_DB_DIR") error("GDS root"))',
        'unless(lvsStageProjectDir("cdl")==getShellEnvVar("CDL_DB_DIR") error("CDL root"))',
        'foreach(flow list("DRC" "LVS" "RCE" "LEF")',
        '  unless(SICO_profileDefaultPath(flow)==getShellEnvVar(strcat(flow "_CONFIG"))',
        '    error("profile root")))',
    ]
    # Access each defstruct using its proper type; compare shared CPU options.
    for struct in ("drcStruc", "lvsStruc", "extstrStruc", "lefStruc", "streamGdsStruc", "exportCdlStruc"):
        code.append(f'unless(equal({struct}->cpuChoices list("1" "3" "6")) error("shared CPUs: {struct}"))')
    code += [
        'unless(extstrStruc->extToolDefValue=="QRC" error("RCE tool"))',
        'unless(extstrStruc->tempDefvalue=="37" error("RCE temperature"))',
        'unless(equal(extstrStruc->outTypeChoices list("dspf" "view")) error("RCE outputs"))',
        'unless(rcePreferredProcessCorner(list("Typ" "Probe") nil)=="Probe" error("RCE corner"))',
        'unless(lefStruc->optionsFile==getShellEnvVar("LEFGEN_OPT_FILE") error("LEF options"))',
        'unless(lefStruc->cellListFile==getShellEnvVar("LEF_CELL_LIST") error("LEF cells"))',
    ]
    for name in ("DRC_FILE", "LVS_FILE", "RCE_LVS_FILE"):
        code.append(f'unless(SICO_envPairValue("{name}" "probe")=="{tmp_path}/{name}" error("rule list"))')
    for tool, name in (("QRC", "QUANTUS_TECH_DIR"), ("StarRC", "STARRC_TECH_DIR"), ("CalXRC", "CALXRC_TECH_DIR")):
        code.append(f'unless(RCE_extTechValue("{tool}" "probe")=="{tmp_path}/{name}" error("tech list"))')
    for display, form, flow in (
        ("cadDisplayDrcForm", "drcForm", "DRC"),
        ("cadDisplayLvsForm", "lvsForm", "LVS"),
        ("cadDisplayRceForm", "rceForm", "RCE"),
        ("cadDisplayLefForm", "lefForm", "LEF"),
        ("cadDisplayStreamGdsForm", "streamGdsForm", "GDS"),
        ("cadDisplayExportCdlForm", "exportCdlForm", "CDL"),
    ):
        code += [f'{display}()',
                 f'unless({form}~>outDirPath~>value==getShellEnvVar("{flow}_DB_DIR") error("{flow} form root"))']
        if flow in ("DRC", "LVS", "RCE", "LEF"):
            code.append(f'unless({form}~>profileFile~>value==getShellEnvVar("{flow}_CONFIG") error("{flow} form profile"))')
        if flow in ("LVS", "RCE"):
            code.append(f'unless({form}~>lvsHcellFile~>value==getShellEnvVar("HCELL_FILE") error("Hcell field"))')
        code.append(f'hiFormDone({form})')
    code += [
        'setShellEnvVar("EXT_CPU_ALLOW" "stale")',
        'setShellEnvVar("SICO_FLOW_CPU_ALLOW" "")',
        'setShellEnvVar("EXT_TEMP_LIST" "stale")',
        'setShellEnvVar("RCE_TEMP_LIST" "")',
        'setShellEnvVar("EXT_OUTPUT_CHOICES" "stale")',
        'setShellEnvVar("RCE_OUTPUT_CHOICES" "")',
        'rceInitializeConfig()',
        'unless(equal(extstrStruc->cpuChoices list("1" "2" "4" "8")) error("empty CPU defaults"))',
        'unless(equal(extstrStruc->tempChoices list("125" "85" "25")) error("empty temperatures"))',
        'unless(equal(extstrStruc->outTypeChoices list("dspf")) error("empty output defaults"))',
        'printf("FLOW_FRONTENDS_PASS\\n")',
    ]
    # Inspect SKILL++ state in its own language, not the CIW SKILL value slots.
    probe = tmp_path / "frontends.ils"
    probe.write_text("\n".join(code) + "\n")
    output = run_virtuoso_source(f'load({json.dumps(str(probe))})\nexit()', tmp_path,
                                 env_updates=env, log_path=tmp_path / "frontends.log")
    assert "FLOW_FRONTENDS_PASS" in output


@pytest.mark.parametrize("mode", ["old", "new", "both", "empty", "explicit"])
def test_lsf_kill_command_migration(tmp_path, monkeypatch, capsys, mode):
    monkeypatch.delenv("SICO_LSF_BKILL", raising=False)
    monkeypatch.setenv("EXT_LSF_BKILL", "private-old-value")
    seen = []
    monkeypatch.setattr("cadlsf.gui.app.run_gui", lambda **kw: seen.append(kw["config"].bkill) or 0)
    args = ["monitor"]
    if mode in ("new", "both", "empty"):
        monkeypatch.setenv("SICO_LSF_BKILL", "" if mode == "empty" else "/new/bkill")
    if mode == "new":
        monkeypatch.delenv("EXT_LSF_BKILL")
    if mode == "explicit":
        args = ["--bkill", "/explicit/bkill", *args]
    result = main(args)
    if mode == "old":
        assert result == 2 and not seen
        error = capsys.readouterr().err
        assert "EXT_LSF_BKILL was renamed; set SICO_LSF_BKILL" in error
        assert "private-old-value" not in error
    else:
        assert result == 0
        assert seen == [{"empty": "bkill", "explicit": "/explicit/bkill"}.get(mode, "/new/bkill")]
