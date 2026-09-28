from __future__ import annotations

from pathlib import Path

import pytest
from absolute_path_probe_support import (
    RUN_PROBE,
    _install_tree,
    _run_probe,
    _write,
    tomllib,
)


@pytest.mark.skipif(not RUN_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
def test_drc_writer_makes_selected_paths_absolute(tmp_path: Path) -> None:
    install = _install_tree(tmp_path)
    launch = tmp_path / "launch"
    run_root = tmp_path / "runs"
    run_root.mkdir()
    _write(launch / "cds.lib")
    runset = _write(launch / "rules/drc.cal")
    layer_map = _write(launch / "maps/layers.map")

    _run_probe(
        tmp_path,
        flow="DRC",
        entry=f"{install}/tools/drc/skill++/DRC.ils",
        commands=(
            "cadDisplayDrcForm()",
            "form=drcForm",
            'form~>layLib~>value="probe"',
            'form~>layCell~>value="top"',
            'form~>layView~>value="layout"',
            f'form~>outDirPath~>value="{run_root}"',
            'form~>drcRunsetFile~>value="rules/drc.cal"',
            "form~>drcRuleSelectEnable~>value=t",
            'form~>drcRuleSelectGroups=list("GAA" "GGT")',
            'form~>drcRuleSelectChecks=list("GT_1" "GT_2")',
            'form~>drcRuleSelectRuleFile=SICO_absolutePath("rules/drc.cal")',
            "form~>customSvrfEnable~>value=t",
            'form~>customSvrfCommand~>value="DRC CHECK MAP"',
            "config=drcWriteToml(form)",
            "unless(config error(\"DRC TOML was not written\"))",
            "when(form hiFormDone(form) hiDeleteForm(form))",
        ),
        env_updates={
            "CAD_HOME": str(install),
            "CDS_LIB": "cds.lib",
            "TECH_LAYER_MAP": "maps/layers.map",
            "DRC_DB_DIR": str(run_root),
            "DRC_FILE": "probe,rules/drc.cal",
        },
    )

    config = tomllib.loads((run_root / "probe.top/drc.toml").read_text())
    assert config["run"]["cds_lib"] == str(launch / "cds.lib")
    assert config["input"]["layout"]["layer_map"] == str(layer_map)
    assert config["drc"]["runset_file"] == str(runset)
    assert config["drc"]["rule_select_enable"] is True
    assert config["drc"]["rule_select_groups"] == ["GAA", "GGT"]
    assert config["drc"]["rule_select_checks"] == ["GT_1", "GT_2"]
    assert config["drc"]["custom_svrf_enable"] is True
    assert config["drc"]["custom_svrf_command"] == "DRC CHECK MAP"


@pytest.mark.skipif(not RUN_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
def test_lvs_writer_makes_selected_paths_absolute(tmp_path: Path) -> None:
    install = _install_tree(tmp_path)
    launch = tmp_path / "launch"
    run_root = tmp_path / "runs"
    run_root.mkdir()
    _write(launch / "cds.lib")
    runset = _write(launch / "rules/lvs.cal")
    layer_map = _write(launch / "maps/layers.map")
    hcell = _write(launch / "lists/hcell.list")
    include = _write(launch / "headers/include.cdl")

    _run_probe(
        tmp_path,
        flow="LVS",
        entry=f"{install}/tools/lvs/skill++/LVS.ils",
        commands=(
            "cadDisplayLvsForm()",
            "form=lvsForm",
            'form~>inpType~>value="OA"',
            "lvsInpCB(form)",
            'form~>schLib~>value="probe"',
            'form~>schCell~>value="top"',
            'form~>schView~>value="schematic"',
            'form~>layLib~>value="probe"',
            'form~>layCell~>value="top"',
            'form~>layView~>value="layout"',
            f'form~>outDirPath~>value="{run_root}"',
            'form~>lvsRunsetFile~>value="rules/lvs.cal"',
            "form~>lvsHcellBtn~>value=t",
            'form~>lvsHcellFile~>value="lists/hcell.list"',
            "form~>cdlIncludeBtn~>value=t",
            'form~>cdlIncludeFile~>value="headers/include.cdl"',
            "config=lvsWriteToml(form)",
            "unless(config error(\"LVS TOML was not written\"))",
            "when(form hiFormDone(form) hiDeleteForm(form))",
        ),
        env_updates={
            "CAD_HOME": str(install),
            "CDS_LIB": "cds.lib",
            "TECH_LAYER_MAP": "maps/layers.map",
            "LVS_DB_DIR": str(run_root),
            "LVS_FILE": "probe,rules/lvs.cal",
            "HCELL_FILE": "lists/hcell.list",
            "CDL_HEADER_FILE": "headers/include.cdl",
        },
    )

    config = tomllib.loads((run_root / "probe.top/lvs.toml").read_text())
    assert config["run"]["cds_lib"] == str(launch / "cds.lib")
    assert config["input"]["layout"]["layer_map"] == str(layer_map)
    assert config["input"]["schematic"]["cdl_header_file"] == str(include)
    assert config["lvs"]["runset_file"] == str(runset)
    assert config["lvs"]["hcell_file"] == str(hcell)


@pytest.mark.skipif(not RUN_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
@pytest.mark.parametrize(
    "colon,name,user_pin_order",
    [(False, False, True), (True, False, True), (False, True, True),
     (True, True, True), (False, False, False)],
)
def test_rce_writer_makes_selected_paths_absolute(
    tmp_path: Path, colon: bool, name: bool, user_pin_order: bool
) -> None:
    install = _install_tree(tmp_path)
    launch = tmp_path / "launch"
    run_root = tmp_path / "runs"
    run_root.mkdir()
    _write(launch / "cds.lib")
    runset = _write(launch / "rules/rce_lvs.cal")
    layer_map = _write(launch / "maps/layers.map")
    hcell = _write(launch / "lists/hcell.list")
    include = _write(launch / "headers/include.cdl")
    tech = launch / "tech/RCmax"
    _write(tech / "qrcTechFile")
    nets = _write(launch / "selections/nets.list", "VDD\n")
    cells = _write(launch / "selections/cells.list", "block\n")
    pins = _write(launch / "selections/pins.cdl")

    _run_probe(
        tmp_path,
        flow="RCE",
        entry=f"{install}/tools/rce/skill++/RCE.ils",
        commands=(
            f'ddCreateLib("probe" "{launch / "probe"}")',
            'probeCv=dbOpenCellViewByType("probe" "top" "schematic" "schematic" "w")',
            'dbSave(probeCv) dbClose(probeCv)',
            "cadDisplayRceForm()",
            "form=rceForm",
            'form~>inpType~>value="OA"',
            "rceInpCB(form)",
            'form~>schLib~>value="probe"',
            'form~>schCell~>value="top"',
            'form~>schView~>value="schematic"',
            'form~>layLib~>value="probe"',
            'form~>layCell~>value="top"',
            'form~>layView~>value="layout"',
            f'form~>outDirPath~>value="{run_root}"',
            'form~>extTool~>value="QRC"',
            'form~>rcTechDirPath~>value="tech/RCmax"',
            "rceRcTechDirPathCB(form)",
            'form~>outType~>value="dspf"',
            "rceOutTypeCB(form)",
            'form~>lvsRunsetFile~>value="rules/rce_lvs.cal"',
            "form~>lvsHcellBtn~>value=t",
            'form~>lvsHcellFile~>value="lists/hcell.list"',
            "form~>cdlIncludeBtn~>value=t",
            'form~>cdlIncludeFile~>value="headers/include.cdl"',
            "form~>netSelBtn~>value=t",
            'form~>netSelFile~>value="selections/nets.list"',
            "form~>cellSelBtn~>value=t",
            'form~>cellSelFile~>value="selections/cells.list"',
            *(
                (
                    'form~>pinOrderType~>value="User Defined File"',
                    'form~>pinOrderFile~>value="selections/pins.cdl"',
                ) if user_pin_order else ()
            ),
            f'form~>lvsVirtualConn~>value=list({"t" if colon else "nil"} {"t" if name else "nil"})',
            f'unless(form~>lvsVirtualConnName~>invisible=={"nil" if name else "t"} error("Virtual Connect callback used the wrong form"))',
            "config=rcePrint(form)",
            "unless(config error(\"RCE TOML was not written\"))",
            "when(form hiFormDone(form) hiDeleteForm(form))",
        ),
        env_updates={
            "CAD_HOME": str(install),
            "CDS_LIB": "cds.lib",
            "TECH_LAYER_MAP": "maps/layers.map",
            "RCE_DB_DIR": str(run_root),
            "RCE_LVS_FILE": "probe,rules/rce_lvs.cal",
            "HCELL_FILE": "lists/hcell.list",
            "CDL_HEADER_FILE": "headers/include.cdl",
            "QUANTUS_TECH_DIR": "probe,tech/RCmax",
            "RCE_DEF_TOOL": "QRC",
            "RCE_OUTPUT_CHOICES": "dspf",
            "RCE_CORNER": "RCmax",
        },
    )

    config = tomllib.loads((run_root / "probe.top/rce.toml").read_text())
    assert config["run"]["cds_lib"] == str(launch / "cds.lib")
    assert config["input"]["layout"]["layer_map"] == str(layer_map)
    assert config["input"]["schematic"]["cdl_header_file"] == str(include)
    assert config["lvs"]["runset_file"] == str(runset)
    assert config["lvs"]["hcell_file"] == str(hcell)
    assert config["extract"]["tech_dir"] == str(tech)
    assert config["selection"]["nets"] == str(nets)
    assert config["selection"]["cells"] == str(cells)
    assert config["netlist"]["pin_order_enable"] is True
    assert config["netlist"]["pin_order_type"] == (
        "User Defined File" if user_pin_order else "CDL Netlist File"
    )
    assert config["netlist"]["pin_order_file"] == (str(pins) if user_pin_order else "")
    assert config["lvs"]["virtual_connect_enable"] is colon
    assert config["lvs"]["virtual_connect_name_enable"] is name

    # Feed the real GUI-written TOML into the Python generator, rather than
    # assuming the toggle list's serialized representation.
    from rcepy.config import load_config
    from rcepy.gen_lvs import generate_lvs

    cfg = load_config(run_root / "probe.top/rce.toml")
    text = generate_lvs(cfg, cfg.context()).read_text()
    assert ("VIRTUAL CONNECT COLON YES" in text) == colon
    assert ('VIRTUAL CONNECT NAME "?"' in text) == name
    assert next(line for line in text.splitlines() if line.startswith("MASK SVDB")) == (
        f'MASK SVDB DIRECTORY "{cfg.context().svdb_dir}" QUERY CCI'
    )
