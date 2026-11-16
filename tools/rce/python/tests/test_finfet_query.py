"""Calibre Query filename syntax shared by FinFET extraction engines."""

from dataclasses import replace
from pathlib import Path

import pytest

from finfet_support import config
from rcepy.gen_query import generate_query


_FILE_COMMANDS = (
    "response file", "gds write", "layout nametable write",
    "layout netlist write", "layout separated properties write",
    "net xref write", "instance xref write", "cell extents write",
    "port table cells write", "port table write",
    "lvs settings report write", "source hierarchy write", "layout hierarchy write",
)


@pytest.mark.parametrize("tool", ["StarRC", "QRC"])
@pytest.mark.parametrize("external_cci", [False, True])
def test_query_outputs_resolve_from_both_query_and_extractor_directories(
    tmp_path, tool, external_cci,
):
    cfg = config(tmp_path, tool)
    ctx = cfg.context()
    if external_cci:
        ctx = replace(ctx, cci_dir=str(tmp_path / "external cci files"))
    Path(ctx.cci_dir).mkdir(parents=True)
    lines = generate_query(cfg, ctx).read_text().splitlines()
    outputs = {}
    for line in lines:
        for command in _FILE_COMMANDS:
            if line.lower().startswith(command + " "):
                # Calibre Query splits whitespace but preserves quote bytes.
                # shlex.split() would hide the regression by removing quotes.
                fields = line[len(command) + 1:].split()
                filename = fields[0]
                assert '"' not in filename and "'" not in filename
                path = Path(filename)
                assert path.is_absolute()
                assert path.parent.resolve() == Path(ctx.cci_dir).resolve()
                assert path.name.startswith(ctx.layout_cell + ".") or path.name == "top_pin_xy.spi"
                for cwd in (ctx.run_dir, ctx.log_dir):
                    assert (cwd / path).resolve() == Path(ctx.cci_dir) / path.name
                outputs[path.name] = fields[1:]
                break
    required = {"top.gds.map", "top.agf", "top.devtab", "top.lnn",
                "top.pdsp", "top.nxf", "top.ixf", "top.ports_cells",
                "top.lvs_settings"}
    assert required <= outputs.keys()
    assert len(outputs) == (11 if tool == "StarRC" else 13)
    assert outputs["top.nxf"] == ["BOX", "LNXF"]
    assert outputs["top.lnn"] == ["EXPAND_CELLS"]
    if external_cci:
        aliases = list((ctx.log_dir / "query_paths").iterdir())
        assert len(aliases) == 1 and aliases[0].is_symlink()
        assert aliases[0].resolve() == Path(ctx.cci_dir)


def test_qrc_enables_separate_properties_before_pin_location_export(tmp_path):
    cfg = config(tmp_path, "QRC")
    ctx = cfg.context()
    lines = generate_query(cfg, ctx).read_text().splitlines()
    separated = "layout netlist separated properties YES"
    assert lines.count(separated) == 1
    assert lines.index(separated) < lines.index("layout netlist pin locations YES")
    assert lines.index("layout netlist pin locations YES") < lines.index(
        f"layout netlist write {ctx.cci_dir}/top_pin_xy.spi"
    ) < lines.index(f"layout separated properties write {ctx.cci_dir}/top.pdsp")


@pytest.mark.parametrize("tool", ["StarRC", "QRC"])
def test_query_rejects_ambiguous_working_directory(tmp_path, tool):
    cfg = config(tmp_path, tool).replace("run", "run_dir", value=str(tmp_path / "run files"))
    with pytest.raises(ValueError, match="run directory without whitespace"):
        generate_query(cfg, cfg.context())


@pytest.mark.parametrize("tool", ["StarRC", "QRC"])
@pytest.mark.parametrize("cell", ["top cell", "top\ncell"])
def test_query_rejects_whitespace_in_cell_names(tmp_path, tool, cell):
    cfg = config(tmp_path, tool)
    with pytest.raises(ValueError, match="cell name without whitespace"):
        generate_query(cfg, replace(cfg.context(), layout_cell=cell))


@pytest.mark.parametrize("character", ['"', "'", "\\"])
def test_query_rejects_literal_filename_delimiters(tmp_path, character):
    cfg = config(tmp_path, "StarRC")
    ctx = replace(cfg.context(), cci_dir=str(tmp_path / ("cci" + character + "dir")))
    with pytest.raises(ValueError, match="quotes or backslashes"):
        generate_query(cfg, ctx)
