from pathlib import Path
import shlex

import pytest

from finfet_support import config, write
from rcepy.gen_query import generate_query
from rcepy.gen_qrc import generate_qrc
from rcepy.gen_starrc import generate_starrc
from rcepy.generators import generate_all


def test_starrc_imports_only_process_settings_and_refreshes_query(tmp_path):
    cfg = config(tmp_path, "StarRC")
    outputs = generate_all(cfg, cfg.context())
    text = outputs["extract"].read_text()
    assert "MULTIGATE_MODELS: YES" in text
    assert text.count("LPE_PARAM:") == text.count("LPE_DEVICES:") == 2
    assert "wrong_design" not in text and "REDUCTION: NO" not in text
    for key in ("TCAD_GRD_FILE", "MAPPING_FILE", "CALIBRE_OPTIONAL_DEVICE_PIN_FILE"):
        value = next(line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith(key + ":"))
        assert len(value.split()) == 1 and '"' not in value
        assert (cfg.run_dir / value).is_file()
    query = outputs["query"].read_text()
    assert "GDS SEED PROPERTY DEVICE ORIGINAL" in query
    assert "LAYOUT NETLIST SEPARATED PROPERTIES NO" in query
    assert f"LAYOUT SEPARATED PROPERTIES WRITE {cfg.context().cci_dir}/top.pdsp\n" in query
    assert "EXPAND_CELLS" in query
    cfg = cfg.replace("input", value={"type": "CCI", "cci": {"cell": "top", "dir": "input cci"}})
    outputs["query"].write_text("stale planar query\n")
    text = generate_starrc(cfg, cfg.context()).read_text()
    assert "lvs_process/000_lvs.cal" in text
    assert "stale" not in outputs["query"].read_text()


def test_starrc_explicit_technology_files_need_no_directory(tmp_path):
    cfg = config(tmp_path, "StarRC")
    tech = Path(cfg.path("extract", "tech_dir"))
    cfg = cfg.replace("extract", "tech_dir", value="")
    for field, name in (("tcad_grd_file", "starrcxt.nxtgrd"),
                        ("mapping_file", "starrcxt_mapping"), ("reference_file", "star_cmd")):
        cfg = cfg.replace("extract", "starrc", field, value=str(tech / name))
    assert "MULTIGATE_MODELS: YES" in generate_starrc(cfg, cfg.context()).read_text()


def test_starrc_missing_pin_and_conflicting_common_are_rejected(tmp_path):
    cfg = config(tmp_path, "StarRC")
    tech = Path(cfg.path("extract", "tech_dir"))
    pin = tech / "pin_file.txt"
    pin.unlink()
    with pytest.raises(FileNotFoundError, match="device pin file"):
        generate_starrc(cfg, cfg.context())
    write(pin)
    write(tech / "common.opt", "MULTIGATE_MODELS: NO\n")
    with pytest.raises(ValueError, match="FinFET StarRC technology"):
        generate_starrc(cfg, cfg.context())


def test_starrc_multi_corner_compares_pin_contents_not_location(tmp_path):
    cfg = config(tmp_path, "StarRC")
    tech = Path(cfg.path("extract", "tech_dir"))
    for name in ("slow", "fast"):
        for file in ("star_cmd", "pin_file.txt", "starrcxt.nxtgrd", "starrcxt_mapping"):
            write(tech / name / file, (tech / file).read_text())
    cfg = cfg.replace("extract", "corners", value=["slow", "fast"])
    assert "SIMULTANEOUS_MULTI_CORNER: YES" in generate_starrc(cfg, cfg.context()).read_text()
    write(tech / "fast/pin_file.txt", "different\n")
    with pytest.raises(ValueError, match="identical shared technology"):
        generate_starrc(cfg, cfg.context())


def test_quantus_companions_query_and_run_specific_capgen(tmp_path):
    cfg = config(tmp_path)
    cfg = cfg.replace("selection", value={"cell_enable": True, "cell_type": "Block Cells",
                                          "cells": ["synthetic_one", "my_block"]})
    outputs = generate_all(cfg, cfg.context())
    text = outputs["extract"].read_text()
    assert '-coupling advanced' in text and '-max_via_array_count 4' in text
    assert '-max_via_array_size' not in text
    assert '-background_density_map all 50' in text
    assert '-technology_lpe_confile' in text
    tokens = shlex.split(text.replace("\\\n", " "))
    technology = tokens[tokens.index('-technology_directory') + 1]
    assert (cfg.run_dir / technology).resolve() == Path(cfg.path('extract', 'tech_dir')) / 'Tech/typical'
    assert '-device_properties_file "' in text and '.pdsp"' in text
    for name, value in (("net", 5), ("instance", 6), ("device", 7)):
        assert f'-{name}_property_value {value}' in text
    tokens = shlex.split((cfg.context().log_dir / "qrc_process.capgen.cmd").read_text())
    assert tokens[tokens.index("-lvs_query_directory") + 1] == cfg.context().cci_dir
    assert tokens[tokens.index("-lvs_run_name") + 1] == "top"
    cells = (cfg.context().log_dir / "cells.merged").read_text()
    assert cells == "synthetic_*\nmy_block\n"
    query = outputs["query"].read_text()
    assert f"layout separated properties write {cfg.context().cci_dir}/top.pdsp\n" in query
    assert '.props' not in query
    assert query.count('layout netlist separated properties YES') == 1


def test_quantus_multi_corner_requires_shared_companions(tmp_path):
    cfg = config(tmp_path)
    tech = Path(cfg.path("extract", "tech_dir"))
    write(tech / "Tech/slow/qrcTechFile")
    cfg = cfg.replace("extract", "corners", value=["typical", "slow"])
    generate_qrc(cfg, cfg.context())
    defs = (cfg.context().log_dir / "qrc_mpc_technology/corner.defs").read_text()
    slow = next(line.split(maxsplit=2)[2] for line in defs.splitlines() if line.startswith("DEFINE slow "))
    assert (cfg.run_dir / slow).resolve() == tech / "Tech/slow"
    write(tech / "Tech/slow/layer_setup", "incompatible\n")
    with pytest.raises(ValueError, match="incompatible layer_setup"):
        generate_qrc(cfg, cfg.context())


@pytest.mark.parametrize(
    ("file", "option"),
    [
        ("capgen.cmd", "-technology_command_file"),
        ("layer_setup", "-technology_layer_setup_file"),
        ("lpe_confile", "-technology_lpe_confile"),
        ("hcell_qci", "-parasitic_blocking_device_cells_file"),
    ],
)
def test_quantus_missing_companions_are_optional(tmp_path, file, option):
    cfg = config(tmp_path)
    (Path(cfg.path("extract", "tech_dir")) / file).unlink()
    text = generate_qrc(cfg, cfg.context()).read_text()
    assert option not in text


def test_quantus_rejects_space_in_working_directory(tmp_path):
    cfg = config(tmp_path).replace("run", "run_dir", value=str(tmp_path / "run files"))
    with pytest.raises(ValueError, match="run directory without whitespace"):
        generate_qrc(cfg, cfg.context())


@pytest.mark.parametrize("tool", ["QRC", "StarRC"])
def test_multi_corner_output_rejects_spaced_basename(tmp_path, tool):
    cfg = config(tmp_path, tool)
    tech = Path(cfg.path("extract", "tech_dir"))
    if tool == "QRC":
        write(tech / "Tech/slow/qrcTechFile")
        cfg = cfg.replace("extract", "corners", value=["typical", "slow"])
    else:
        for name in ("slow", "fast"):
            for file in ("star_cmd", "pin_file.txt", "starrcxt.nxtgrd", "starrcxt_mapping"):
                write(tech / name / file, (tech / file).read_text())
        cfg = cfg.replace("extract", "corners", value=["slow", "fast"])
    cfg = cfg.replace("netlist", "output_path", value="output files/top output.dspf")
    generate = generate_qrc if tool == "QRC" else generate_starrc
    with pytest.raises(ValueError, match="output filename must not contain whitespace"):
        generate(cfg, cfg.context())
    cfg = cfg.replace("netlist", "output_path", value="output files/top.dspf")
    assert generate(cfg, cfg.context()).is_file()


@pytest.mark.parametrize("tool", ["QRC", "StarRC"])
def test_planar_does_not_import_finfet_process_options(tmp_path, tool):
    cfg = config(tmp_path, tool, mode=False)
    outputs = generate_all(cfg, cfg.context())
    text = outputs["extract"].read_text()
    assert "MULTIGATE_MODELS" not in text and "-technology_lpe_confile" not in text
    assert "source.with_devices" not in outputs["lvs"].read_text()
    query = outputs["query"].read_text().lower()
    assert "layout netlist separated properties yes" in query
