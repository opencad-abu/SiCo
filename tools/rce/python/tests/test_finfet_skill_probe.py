"""Exercise process discovery and TOML serialization in headless Virtuoso."""

import os
from pathlib import Path

import pytest

from finfet_support import config
from rcepy.compat import tomllib
from skill_probe_support import run_virtuoso_source


@pytest.mark.skipif(os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
                    reason="set RCE_RUN_SKILL_PROBE=1")
@pytest.mark.parametrize("mode", ["0", "1"])
def test_process_writer_and_corner_discovery(tmp_path, mode):
    root = Path(__file__).resolve().parents[4]
    cfg = config(tmp_path)
    tech = cfg.path("extract", "tech_dir")
    output = tmp_path / "request.toml"
    source = '\n'.join([
        f'load("{root}/tools/common/skill/SICO_toml.il")',
        f'load("{root}/tools/rce/skill++/RCECORNERPATH.ils")',
        f'load("{root}/tools/rce/skill++/RCEPROCESS.ils")',
        f'load("{root}/tools/rce/skill++/RCETOMLWRITE.ils")',
        f'unless(rceProcessCornerNames("QRC" "{tech}")==list("typical") error("QRC discovery"))',
        f'unless(rceStarCornerDirP("{tech}" "{tech}") error("StarRC discovery"))',
        f'out=outfile("{output}")',
        'SICO_tomlWriteSection(out "extract")',
        'SICO_tomlWriteBool(out "finfet_flow" rceFinfetFlowP())',
        f'rceWriteProcessInputsToml(out "{tech}" "typical")',
        'rceWriteInputTypeToml(out "OA")',
        'rceWriteLayoutToml(out "SicoTest" "top" "layout" "map")',
        'close(out)',
        'printf("RCE_PROCESS_PROBE_OK\\n")',
        'exit()',
    ])
    text = run_virtuoso_source(source, tmp_path, env_updates={
        "SICO_HOME": str(root), "SICO_TEMP_DIR": str(tmp_path / ".sico"),
        "RCE_FINFET_FLOW": mode, "RCE_GDS_OBJECT_MAP": "process files/object map",
        "RCE_CDL_SOURCE_ADDED_FILE": "process files/source.added",
        "RCE_QUANTUS_BLOCK_CELL_FILE": "../../hcell_qci",
    }, log_path=tmp_path / "virtuoso.log")
    assert "RCE_PROCESS_PROBE_OK" in text
    raw = tomllib.loads(output.read_text())
    assert raw["extract"]["finfet_flow"] == (mode == "1")
    assert raw["extract"]["qrc"]["block_cell_file"] == str(Path(tech) / "hcell_qci")
    assert raw["input"]["layout"]["object_map"] == str(Path(tech) / "object map")
    assert raw["input"]["source_added_file"] == str(Path(tech) / "source.added")
