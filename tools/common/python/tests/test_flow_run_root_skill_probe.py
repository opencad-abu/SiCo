"""Probe the flow run-root policy: create a missing project directory on demand.

Every flow writes its TOML below the selected output directory.  That directory
may be a project directory that does not exist yet, so the write path creates
the whole chain and the "Project Directory" callbacks keep the configured path
instead of falling back to the current directory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


CAD_ROOT = Path(__file__).resolve().parents[3]

# The front-ends keep their LOAD/GUI dependencies; these probes replace only the
# LSF selection, the host lookup and the dialogs, so a headless SKILL
# interpreter can exercise the run-root code path of each writer.
PROBE_HEAD = """\
procedure(pwd() getShellEnvVar("PROBE_RUNTIME"))
load(strcat(getShellEnvVar("SICO_HOME") "/tools/" getShellEnvVar("PROBE_ENTRY")))

probeMessages=nil
procedure(GUI_messageApp(severity flow message)
  probeMessages=cons(strcat(flow ": " message) probeMessages)
  nil
)
procedure(SICO_lsfValidateSelection(form flow) t)
procedure(SICO_lsfSelectedHost(value) value)
defstruct(probeField value)
procedure(probeValue(value) make_probeField(?value value))
"""

# Every writer probe reports the same three scenarios: a missing project run
# root is created, the "Please select a directory..." placeholder is rejected
# without materializing it, and an un-creatable root keeps the access error.
PROBE_SCENARIOS = """
probeMessages=nil
placeholderConfig={writer}({placeholder_args})
printf("PROBE_PLACEHOLDER=%L\\n" placeholderConfig)
printf("PROBE_PLACEHOLDER_STRAY=%L\\n"
  isDir(strcat(pwd() "/Please select a directory...")))
printf("PROBE_PLACEHOLDER_MESSAGES=%L\\n" probeMessages)

probeMessages=nil
blockedConfig={writer}({blocked_args})
printf("PROBE_BLOCKED=%L\\n" blockedConfig)
printf("PROBE_BLOCKED_MESSAGES=%L\\n" probeMessages)
exit()
"""

DRC_WRITE = PROBE_HEAD + """\
defstruct(probeForm outDirPath layLib layCell layView drcRunsetFile drcRunsetName
  drcTool drcRunMode drcRuleSelectEnable runType queueName srvName runCpu
  customSvrfEnable customSvrfCommand)
procedure(probeFormMake(root)
  make_probeForm(
    ?outDirPath probeValue(root)
    ?layLib probeValue("sxTest") ?layCell probeValue("busTest")
    ?layView probeValue("layout")
    ?drcRunsetFile probeValue(getShellEnvVar("PROBE_RUNSET"))
    ?drcRunsetName probeValue("probe")
    ?drcTool probeValue("Calibre") ?drcRunMode probeValue("Hier")
    ?drcRuleSelectEnable probeValue(nil)
    ?runType probeValue("Current Host") ?queueName probeValue("")
    ?srvName probeValue("localhost") ?runCpu probeValue("1")
    ?customSvrfEnable probeValue(nil) ?customSvrfCommand probeValue("")))

probeMessages=nil
config=drcWriteToml(probeFormMake(getShellEnvVar("PROBE_ROOT")))
printf("PROBE_CONFIG=%L\\n" config)
printf("PROBE_RUN_DIR=%L\\n"
  isDir(strcat(getShellEnvVar("PROBE_ROOT") "/sxTest.busTest")))
printf("PROBE_MESSAGES=%L\\n" probeMessages)
""" + PROBE_SCENARIOS.format(
    writer="drcWriteToml",
    placeholder_args='probeFormMake("Please select a directory...")',
    blocked_args='probeFormMake(getShellEnvVar("PROBE_BLOCKER"))',
)

LVS_WRITE = PROBE_HEAD + """\
defstruct(probeForm outDirPath inpType schLib schCell schView layLib layCell
  layView cdlFile cdlCell gdsFile gdsCell lvsRunsetFile lvsRunsetName lvsTool
  lvsRunMode lvsCaseBtn lvsHcellBtn lvsHcellFile lvsVirtualConn
  lvsVirtualConnName lvsRecognizeGates lvsCpu cdlIncludeBtn cdlIncludeFile
  customSvrfEnable customSvrfCommand runType queueName srvName)
procedure(probeFormMake(root)
  make_probeForm(
    ?outDirPath probeValue(root)
    ?inpType probeValue("OA")
    ?schLib probeValue("sxTest") ?schCell probeValue("busTest")
    ?schView probeValue("schematic")
    ?layLib probeValue("sxTest") ?layCell probeValue("busTest")
    ?layView probeValue("layout")
    ?cdlFile probeValue("") ?cdlCell probeValue("")
    ?gdsFile probeValue("") ?gdsCell probeValue("")
    ?lvsRunsetFile probeValue(getShellEnvVar("PROBE_RUNSET"))
    ?lvsRunsetName probeValue("probe")
    ?lvsTool probeValue("Calibre") ?lvsRunMode probeValue("Hier")
    ?lvsCaseBtn probeValue(nil) ?lvsHcellBtn probeValue(nil)
    ?lvsHcellFile probeValue("") ?lvsVirtualConn probeValue(nil)
    ?lvsVirtualConnName probeValue("") ?lvsRecognizeGates probeValue(nil)
    ?lvsCpu probeValue("1")
    ?cdlIncludeBtn probeValue(nil) ?cdlIncludeFile probeValue("")
    ?customSvrfEnable probeValue(nil) ?customSvrfCommand probeValue("")
    ?runType probeValue("Current Host") ?queueName probeValue("")
    ?srvName probeValue("localhost")))

probeMessages=nil
config=lvsWriteToml(probeFormMake(getShellEnvVar("PROBE_ROOT")))
printf("PROBE_CONFIG=%L\\n" config)
printf("PROBE_RUN_DIR=%L\\n"
  isDir(strcat(getShellEnvVar("PROBE_ROOT") "/sxTest.busTest")))
printf("PROBE_MESSAGES=%L\\n" probeMessages)
""" + PROBE_SCENARIOS.format(
    writer="lvsWriteToml",
    placeholder_args='probeFormMake("Please select a directory...")',
    blocked_args='probeFormMake(getShellEnvVar("PROBE_BLOCKER"))',
)

STAGE_WRITE = PROBE_HEAD + """\
defstruct(probeForm outDirPath inpType schLib schCell schView layLib layCell
  layView replaceBusBitChar cdlIncludeBtn cdlIncludeFile runType queueName
  srvName runCpu)
procedure(probeFormMake(root)
  make_probeForm(
    ?outDirPath probeValue(root)
    ?inpType probeValue("OA")
    ?schLib probeValue("sxTest") ?schCell probeValue("busTest")
    ?schView probeValue("schematic")
    ?layLib probeValue("sxTest") ?layCell probeValue("busTest")
    ?layView probeValue("layout")
    ?replaceBusBitChar probeValue(t)
    ?cdlIncludeBtn probeValue(nil) ?cdlIncludeFile probeValue("")
    ?runType probeValue("Current Host") ?queueName probeValue("")
    ?srvName probeValue("localhost") ?runCpu probeValue("1")))

probeMessages=nil
config=lvsStageWriteToml(probeFormMake(getShellEnvVar("PROBE_ROOT"))
  getShellEnvVar("PROBE_STAGE"))
printf("PROBE_CONFIG=%L\\n" config)
printf("PROBE_RUN_DIR=%L\\n" isDir(getShellEnvVar("PROBE_EXPECT_RUN_DIR")))
printf("PROBE_MESSAGES=%L\\n" probeMessages)
""" + PROBE_SCENARIOS.format(
    writer="lvsStageWriteToml",
    placeholder_args=(
        'probeFormMake("Please select a directory...")'
        ' getShellEnvVar("PROBE_STAGE")'
    ),
    blocked_args='probeFormMake(getShellEnvVar("PROBE_BLOCKER")) getShellEnvVar("PROBE_STAGE")',
)

RCE_WRITE = PROBE_HEAD + """\
procedure(rceValidatePinOrder(form) t)
procedure(rceValidateCdlRunDirectory(form) t)
procedure(rceValidateReduction(form) t)
procedure(rceCheckOaTermOrder(form) t)
procedure(rceCdsLibPath(form) getShellEnvVar("PROBE_CDSLIB"))
procedure(rceSyncExtractTopCell(form) "busTest")
procedure(rceProcessCornerNames(tool techDir) list("Typ"))
procedure(rceSelectedProcessCorners(form) list("Typ"))
procedure(rceSelectedCornerTemperatures(form) list("25"))
procedure(rceMatchingProcessCorner(corner available) corner)
procedure(rceNativeViewOutputP(outType) nil)
procedure(rceParasiticNetlistOutputP(outType) nil)
procedure(RCE_tomlDefaultOutput(runDir topCell outType) "")
procedure(rceWriteInputTypeToml(outFile inputType) nil)
procedure(rceWriteSchematicToml(outFile schlib schcell schview cdl) nil)
procedure(rceWriteLayoutToml(outFile laylib laycell layview layerMap) nil)
procedure(rceWriteCdlToml(outFile form cdl cdlcell) nil)
procedure(rceWriteGdsToml(outFile gds gdscell) nil)
procedure(rceWriteSvdbToml(outFile svdb svdbcell) nil)
procedure(rceWriteCciToml(outFile cci ccicell) nil)
procedure(rceWriteLvsToml(outFile form runsetFile hcellFile) nil)
procedure(rceWriteExtractToml(outFile form techDir corner corners temperatures) nil)
procedure(rceWriteViewToml(outFile form nativeView target device layer) nil)
procedure(rceWriteReductionToml(outFile form) nil)
procedure(rceWriteRuntimeToml(outFile form) nil)
procedure(rceWriteSelectionToml(outFile form nets cells) nil)
procedure(rceWriteFilterToml(outFile form) nil)
procedure(rceWriteNetlistToml(outFile form outPath createView pinFile) nil)

defstruct(probeForm outDirPath inpType schLib schCell schView layLib layCell
  layView cdlFile cdlCell gdsFile gdsCell svdbDir svdbCell cciDir cciCell
  lvsRunsetFile lvsHcellBtn lvsHcellFile cdlIncludeBtn cdlIncludeFile
  rcTechDirPath extTool outType netsSelBtn netsSelFile cellSelBtn cellSelFile
  pinOrderBtn pinOrderType pinOrderFile createNetlistView viewDeviceMap
  viewLayerMap runType queueName srvName)
procedure(probeFormMake(root)
  make_probeForm(
    ?outDirPath probeValue(root)
    ?inpType probeValue("OA")
    ?schLib probeValue("sxTest") ?schCell probeValue("busTest")
    ?schView probeValue("schematic")
    ?layLib probeValue("sxTest") ?layCell probeValue("busTest")
    ?layView probeValue("layout")
    ?cdlFile probeValue("") ?cdlCell probeValue("")
    ?gdsFile probeValue("") ?gdsCell probeValue("")
    ?svdbDir probeValue("") ?svdbCell probeValue("")
    ?cciDir probeValue("") ?cciCell probeValue("")
    ?lvsRunsetFile probeValue(getShellEnvVar("PROBE_RUNSET"))
    ?lvsHcellBtn probeValue(nil) ?lvsHcellFile probeValue("")
    ?cdlIncludeBtn probeValue(nil) ?cdlIncludeFile probeValue("")
    ?rcTechDirPath probeValue("")
    ?extTool probeValue("QRC") ?outType probeValue("Extracted View")
    ?netsSelBtn probeValue(nil) ?netsSelFile probeValue("")
    ?cellSelBtn probeValue(nil) ?cellSelFile probeValue("")
    ?pinOrderBtn probeValue(nil) ?pinOrderType probeValue("")
    ?pinOrderFile probeValue("")
    ?createNetlistView probeValue(nil)
    ?viewDeviceMap probeValue("") ?viewLayerMap probeValue("")
    ?runType probeValue("Current Host") ?queueName probeValue("")
    ?srvName probeValue("localhost")))

probeMessages=nil
config=rcePrintReserved(probeFormMake(getShellEnvVar("PROBE_ROOT")))
printf("PROBE_CONFIG=%L\\n" config)
printf("PROBE_RUN_DIR=%L\\n"
  isDir(strcat(getShellEnvVar("PROBE_ROOT") "/sxTest.busTest")))
printf("PROBE_MESSAGES=%L\\n" probeMessages)
""" + PROBE_SCENARIOS.format(
    writer="rcePrintReserved",
    placeholder_args='probeFormMake("Please select a directory...")',
    blocked_args='probeFormMake(getShellEnvVar("PROBE_BLOCKER"))',
)

CALLBACKS = """\
procedure(pwd() getShellEnvVar("PROBE_RUNTIME"))
foreach(entry list("drc/skill++/DRC.ils" "lvs/skill++/LVS.ils"
                    "rce/skill++/RCE.ils" "lef/skill++/LEF.ils")
  load(strcat(getShellEnvVar("SICO_HOME") "/tools/" entry)))

probeMessages=nil
procedure(GUI_messageApp(severity flow message)
  probeMessages=cons(strcat(flow ": " message) probeMessages)
  nil
)
defstruct(probeField value)
procedure(probeValue(value) make_probeField(?value value))
defstruct(probeForm outDirPath outDirType)
procedure(probeFormMake()
  make_probeForm(?outDirPath probeValue(strcat(pwd()))
    ?outDirType probeValue("Project Directory")))

procedure(probeCallbacks(flow form)
  cond(
    (flow=="DRC" drcOutDirCB(form))
    (flow=="LVS" lvsOutDirCB(form))
    (flow=="Stream GDS" lvsStageOutDirCB(form "gds"))
    (flow=="Export CDL" lvsStageOutDirCB(form "cdl"))
    (flow=="RCE" rceOutDirCB(form))
    (flow=="LEF" lefOutDirCB(form))))
procedure(lefFormValidP(form) t)
procedure(lefInputCB(form) nil)

foreach(flow list("DRC" "LVS" "Stream GDS" "Export CDL" "RCE" "LEF")
  probeMessages=nil
  form=probeFormMake()
  probeCallbacks(flow form)
  printf("PROBE_%s=%L\\n" flow form~>outDirPath~>value)
  printf("PROBE_%s_MESSAGES=%L\\n" flow probeMessages))
exit()
"""

WRITERS = (
    ("DRC", "drc/skill++/DRC.ils", DRC_WRITE, "sxTest.busTest", "drc.toml"),
    ("LVS", "lvs/skill++/LVS.ils", LVS_WRITE, "sxTest.busTest", "lvs.toml"),
    (
        "Stream GDS",
        "lvs/skill++/LVS.ils",
        STAGE_WRITE,
        "sxTest.busTest",
        "stream_gds.toml",
    ),
    (
        "Export CDL",
        "lvs/skill++/LVS.ils",
        STAGE_WRITE,
        "sxTest.busTest",
        "export_cdl.toml",
    ),
    ("RCE", "rce/skill++/RCE.ils", RCE_WRITE, "sxTest.busTest", "rce.toml"),
)


def _run_dbaccess(skill: str, *, cwd: Path, env: dict[str, str]) -> str:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
        cwd=cwd,
        env=env,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output, output
    assert "(reader)" not in output, output
    assert "still unclosed on EOF" not in output, output
    return output


def _probe_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    runset = runtime / "probe.drc"
    runset.write_text("GROUP GAA AA_?\n", encoding="utf-8")
    (runtime / "cds.lib").write_text('INCLUDE "/dev/null"\n', encoding="utf-8")
    (runtime / "blocker").write_text("not a directory\n", encoding="utf-8")
    env = os.environ.copy()
    for name in (
        "CAD_HOME",
        "CAD_TEMP_DIR",
        "SICO_TEMP_DIR",
        "DRC_DB_DIR",
        "LVS_DB_DIR",
        "RCE_DB_DIR",
        "GDS_DB_DIR",
        "CDL_DB_DIR",
        "LEF_DB_DIR",
    ):
        env.pop(name, None)
    env.update(
        {
            "SICO_HOME": str(CAD_ROOT.parent),
            "SICO_PYTHON": "/software/pkgs/python/3.9.13/bin/python3",
            "CDS_LIB": str(runtime / "cds.lib"),
            "PROBE_RUNTIME": str(runtime),
            "PROBE_RUNSET": str(runset),
            "PROBE_CDSLIB": str(runtime / "cds.lib"),
            "PROBE_BLOCKER": str(runtime / "blocker" / "nested"),
        }
    )
    env.update(extra)
    return env


@pytest.mark.parametrize(("flow", "entry", "skill", "run_dir", "toml_name"), WRITERS)
def test_write_path_creates_a_missing_project_run_root(
    tmp_path: Path, flow: str, entry: str, skill: str, run_dir: str, toml_name: str
) -> None:
    run_root = tmp_path / "runtime" / "proj3" / "drc"
    extra = {
        "PROBE_ENTRY": entry,
        "PROBE_ROOT": str(run_root),
        "PROBE_EXPECT_RUN_DIR": str(run_root / run_dir),
    }
    if flow in ("Stream GDS", "Export CDL"):
        extra["PROBE_STAGE"] = "gds" if flow == "Stream GDS" else "cdl"
    env = _probe_env(tmp_path, **extra)

    assert not run_root.exists()  # The project run root starts out missing.
    output = _run_dbaccess(skill, cwd=tmp_path / "runtime", env=env)

    toml = run_root / run_dir / toml_name
    assert f'PROBE_CONFIG="{toml}"' in output
    assert "PROBE_RUN_DIR=t" in output
    assert toml.is_file()
    assert "run_dir" in toml.read_text(encoding="utf-8")
    assert "PROBE_MESSAGES=nil" in output

    # The "Please select a directory..." placeholder must not be materialized.
    assert "PROBE_PLACEHOLDER=nil" in output
    assert "PROBE_PLACEHOLDER_STRAY=nil" in output
    assert 'PROBE_PLACEHOLDER_MESSAGES=("' in output
    assert "Cannot create run directory" in output
    # A run root that cannot be created keeps reporting the access error.
    assert "PROBE_BLOCKED=nil" in output
    assert 'PROBE_BLOCKED_MESSAGES=("' in output
    assert not (tmp_path / "runtime" / "blocker").is_dir()


def test_project_directory_callbacks_keep_a_creatable_run_root(
    tmp_path: Path,
) -> None:
    project_dirs = {
        "DRC": tmp_path / "runtime" / "proj3" / "drc",
        "LVS": tmp_path / "runtime" / "proj3" / "lvs",
        "Stream GDS": tmp_path / "runtime" / "proj3" / "gds",
        "Export CDL": tmp_path / "runtime" / "proj3" / "cdl",
        "RCE": tmp_path / "runtime" / "proj3" / "rce",
        "LEF": tmp_path / "runtime" / "proj3" / "lef",
    }
    env_vars = {
        "DRC": "DRC_DB_DIR",
        "LVS": "LVS_DB_DIR",
        "Stream GDS": "GDS_DB_DIR",
        "Export CDL": "CDL_DB_DIR",
        "RCE": "RCE_DB_DIR",
        "LEF": "LEF_DB_DIR",
    }
    extra: dict[str, str] = {
        env_vars[flow]: str(path) for flow, path in project_dirs.items()
    }
    env = _probe_env(tmp_path, **extra)
    for path in project_dirs.values():
        assert not path.exists()

    output = _run_dbaccess(CALLBACKS, cwd=tmp_path / "runtime", env=env)

    for flow, path in project_dirs.items():
        assert f'PROBE_{flow}="{path}"' in output, flow
        assert f"PROBE_{flow}_MESSAGES=nil" in output, flow
    for path in project_dirs.values():
        assert not path.exists()  # Selecting a directory creates nothing.
