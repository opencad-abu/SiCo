from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

CAD_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(CAD_ROOT / "lvs/python"))

from cadprofile.model import ProfileError, load_profile
from lvspy.runner import LvsRunner
from rcepy.config import RceConfig, load_config
from rcepy.generators import generate_all
from test_absolute_path_skill_probe import _install_tree, _run_probe, _write
from xrc_test_support import make_xrc_config


def _config(tmp_path: Path) -> RceConfig:
    _write(tmp_path / "tech/typ/qrcTechFile", "")
    return RceConfig(
        raw={
            "run": {"run_dir": str(tmp_path / "run")},
            "input": {
                "type": "CDL+GDS",
                "cdl": {"file": "top.cdl", "cell": "top"},
                "gds": {"file": "top.gds", "cell": "top"},
            },
            "lvs": {"tool": "Calibre"},
            "extract": {"tool": "QRC", "tech_dir": str(tmp_path / "tech/typ")},
            "runtime": {"lvs_cpus": "1", "ext_cpus": "1"},
        },
        config_path=tmp_path / "run.toml",
    )


def _generate(cfg: RceConfig, flow: str) -> str:
    outputs = LvsRunner(cfg)._generate() if flow == "LVS" else generate_all(cfg, cfg.context())
    return outputs["extract" if flow == "XRC" else "lvs"].read_text()


@pytest.mark.parametrize("flow", ["LVS", "RCE", "XRC"])
@pytest.mark.parametrize("mode", [None, "NONE", "ALL", "SIMPLE"])
def test_recognize_gates_command(tmp_path: Path, flow: str, mode: str | None) -> None:
    cfg = make_xrc_config(tmp_path)[0] if flow == "XRC" else _config(tmp_path)
    if mode is not None:
        cfg = cfg.replace('lvs', 'recognize_gates', value=mode)
    lines = _generate(cfg, flow).splitlines()
    assert [line for line in lines if line.startswith("LVS RECOGNIZE GATES")] == [
        f"LVS RECOGNIZE GATES {mode or 'NONE'}"
    ]


@pytest.mark.parametrize("flow", ["LVS", "RCE", "XRC"])
def test_recognize_gates_rejects_invalid_keyword(tmp_path: Path, flow: str) -> None:
    cfg = make_xrc_config(tmp_path)[0] if flow == "XRC" else _config(tmp_path)
    cfg = cfg.replace('lvs', 'recognize_gates', value="SAMPLE")
    with pytest.raises(ValueError, match="lvs.recognize_gates"):
        _generate(cfg, flow)


def _profile_text(flow: str, mode: str | None) -> str:
    return (
        '[cad_config]\nformat = "sico-flow-profile"\nversion = 1\n'
        f'flow = "{flow}"\n'
        '[run]\nroot = "/tmp/runs"\n'
        '[input]\ntype = "OA"\n'
        '[lvs]\ntool = "Calibre"\n'
        + (f'recognize_gates = "{mode}"\n' if mode is not None else "")
        + ('[extract]\ntool = "QRC"\ncorner = "typ"\n' if flow == "RCE" else "")
        + '[runtime]\nlvs_cpus = "1"\n'
    )


@pytest.mark.parametrize("flow", ["LVS", "RCE"])
@pytest.mark.parametrize("mode", [None, "NONE", "ALL", "SIMPLE", "SAMPLE"])
def test_profile_recognize_gates(tmp_path: Path, flow: str, mode: str | None) -> None:
    path = _write(tmp_path / "profile.toml", _profile_text(flow, mode))
    if mode == "SAMPLE":
        with pytest.raises(ProfileError, match="lvs.recognize_gates"):
            load_profile(path, flow)
    else:
        assert load_profile(path, flow).as_dict()["lvs.recognize_gates"] == (mode or "NONE")


@pytest.mark.skipif(os.environ.get("RCE_RUN_SKILL_PROBE") != "1", reason="set RCE_RUN_SKILL_PROBE=1")
@pytest.mark.parametrize("flow", ["LVS", "RCE"])
def test_gate_menu_profile_and_real_run_toml(tmp_path: Path, flow: str) -> None:
    install = _install_tree(tmp_path)
    launch = tmp_path / "launch"
    run_root = tmp_path / "runs"
    run_root.mkdir()
    _write(launch / "cds.lib", "")
    _write(launch / "rules/lvs.cal", "// rules\n")
    _write(launch / "tech/typ/qrcTechFile", "")
    lower = flow.lower()
    writer = "lvsWriteToml" if flow == "LVS" else "rcePrint"
    setup = [
        'procedure(SICO_profileMessage(severity flow message) printf("PROFILE_MESSAGE:%s:%s\\n" flow message) nil)',
        f"cadDisplay{flow.title()}Form()",
        f"form={lower}Form",
        'unless(form~>lvsRecognizeGates~>value=="NONE" error("Wrong gate default"))',
        'unless(equal(form~>lvsRecognizeGates~>choices list("NONE" "ALL" "SIMPLE")) error("Wrong gate choices"))',
        'unless(get(form~>lvsVirtualConn \'\\:)~>prompt=="Colon(:)" error("Wrong colon label"))',
        "gateLocation=hiGetLayoutItemIndex(form 'lvsRecognizeGates)",
        "customLocation=hiGetLayoutItemIndex(form 'customSvrf)",
        "unless(and(car(gateLocation)=='lvsMoreContents car(customLocation)=='lvsMoreContents",
        '  cadr(gateLocation)<cadr(customLocation)) error("Wrong gate menu position"))',
        "form~>lvsMore~>value=t",
        'unless(!form~>lvsMoreContents~>invisible error("More LVS Options did not expand"))',
        'form~>inpType~>value="OA"',
        f"{lower}InpCB(form)",
        'form~>schLib~>value="probe"',
        'form~>schCell~>value="top"',
        'form~>schView~>value="schematic"',
        'form~>layLib~>value="probe"',
        'form~>layCell~>value="top"',
        'form~>layView~>value="layout"',
        f'form~>outDirPath~>value="{run_root}"',
        f'form~>lvsRunsetFile~>value="{launch}/rules/lvs.cal"',
        "form~>lvsHcellBtn~>value=nil",
    ]
    if flow == "RCE":
        setup.extend((
            'form~>extTool~>value="QRC"',
            f'form~>rcTechDirPath~>value="{launch}/tech"',
            "rceRcTechDirPathCB(form)",
            'form~>outType~>value="dspf"',
            "rceOutTypeCB(form)",
        ))
    for mode in ("NONE", "ALL", "SIMPLE"):
        profile = tmp_path / f"{mode}-profile.toml"
        snapshot = tmp_path / f"{mode}-run.toml"
        setup.extend((
            f'form~>lvsRecognizeGates~>value="{mode}"',
            "form~>customSvrfEnable~>value=nil",
            "cadCustomSvrfCB(form)",
            'unless(form~>lvsRecognizeGates~>enabled error("Gate menu disabled with custom SVRF"))',
            f'unless(SICO_profileSaveTo(form "{flow}" "{profile}") error("Profile save failed"))',
            'form~>lvsRecognizeGates~>value="ALL"',
            f'form~>profileFile~>value="{profile}"',
            f'unless(SICO_profileLoadCB(form "{flow}") error("Profile load failed"))',
            f'unless(form~>lvsRecognizeGates~>value=="{mode}" error("Gate mode was not restored"))',
            f"config={writer}(form)",
            'unless(config error("Run TOML was not written"))',
            f'system(strcat("/bin/cp -- " SICO_shellQuote(config) " " SICO_shellQuote("{snapshot}")))',
        ))
    setup.extend((
        f'legacy=setof(entry SICO_profileCollect(form "{flow}") car(entry)!="lvs.recognize_gates")',
        f'unless(SICO_profileApply(form "{flow}" legacy) error("Legacy profile apply failed"))',
        'unless(form~>lvsRecognizeGates~>value=="NONE" error("Wrong legacy default"))',
        "hiFormClose(form)",
        "hiDeleteForm(form)",
    ))
    _run_probe(
        tmp_path, flow=flow,
        entry=f"{install}/tools/{lower}/skill++/{flow}.ils",
        commands=tuple(setup),
        env_updates={
            "CAD_HOME": str(install),
            "CAD_PYTHON": sys.executable,
            "CDS_LIB": "cds.lib",
            "LVS_DB_DIR": str(run_root),
            "RCE_DB_DIR": str(run_root),
            "LVS_FILE": "probe,rules/lvs.cal",
            "RCE_LVS_FILE": "probe,rules/lvs.cal",
            "QUANTUS_TECH_DIR": f"probe,{launch}/tech",
            "RCE_DEF_TOOL": "QRC",
            "RCE_OUTPUT_CHOICES": "dspf",
            "RCE_CORNER": "typ",
            "HCELL_FILE": "",
        },
    )
    for mode in ("NONE", "ALL", "SIMPLE"):
        assert load_profile(tmp_path / f"{mode}-profile.toml", flow).as_dict()["lvs.recognize_gates"] == mode
        cfg = load_config(tmp_path / f"{mode}-run.toml")
        assert cfg.text("lvs", "recognize_gates") == mode
        assert f"LVS RECOGNIZE GATES {mode}" in _generate(cfg, flow)
