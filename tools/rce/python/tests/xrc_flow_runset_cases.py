from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.generators import generate_all  # noqa: E402
from xrc_test_support import (  # noqa: E402
    make_xrc_config,
    xrc_stages,
)


def test_generate_xrc_runset_contains_required_svrf(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    rce_lvs_rule = tmp_path / "rce.lvs"
    rce_lvs_rule.write_text("// RCE pre-LVS rules\n", encoding="utf-8")
    cfg = cfg.replace('lvs', 'runset_file', value=str(rce_lvs_rule))

    generated = generate_all(cfg, cfg.context())

    assert generated["extract"] == paths["runset"]
    text = paths["runset"].read_text(encoding="utf-8")
    assert f'LAYOUT PATH "{paths["layout"]}"' in text
    assert 'LAYOUT SYSTEM GDSII' in text
    assert 'LAYOUT PRIMARY "top"' in text
    assert f'SOURCE PATH "{paths["source"]}"' in text
    assert 'SOURCE SYSTEM SPICE' in text
    assert 'SOURCE PRIMARY "top"' in text
    assert f'PEX NETLIST "{paths["output"]}" DSPF SOURCENAMES' in text
    assert f'MASK SVDB DIRECTORY "{paths["svdb"]}" QUERY XRC' in text
    assert 'PEX EXTRACT TEMPERATURE -30' in text
    assert f'INCLUDE "{rce_lvs_rule}"' in text
    assert f'INCLUDE "{paths["corner_dir"] / "xrc.cal"}"' in text
    assert text.index(f'INCLUDE "{rce_lvs_rule}"') < text.index(
        f'INCLUDE "{paths["corner_dir"] / "xrc.cal"}"'
    )
    assert "NOINSTANCESECTION" not in text



def test_xrc_appends_custom_svrf_without_modifying_foundry_deck(
    tmp_path: Path,
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    foundry_deck = paths["corner_dir"] / "xrc.cal"
    original_foundry_text = foundry_deck.read_text(encoding="utf-8")
    custom = 'PEX REDUCE ANALOG YES\n// preserve "user text"\n'
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{
            "custom_svrf_enable": True,
            "custom_svrf_command": custom,
        }})

    generate_all(cfg, cfg.context())

    text = paths["runset"].read_text(encoding="utf-8")
    assert text.endswith("\n\n" + custom)
    assert text.index(f'INCLUDE "{foundry_deck}"') < text.index(custom)
    assert foundry_deck.read_text(encoding="utf-8") == original_foundry_text



def test_xrc_case_matching_defaults_to_strict_and_can_be_disabled(
    tmp_path: Path,
) -> None:
    cfg, paths = make_xrc_config(tmp_path)

    generate_all(cfg, cfg.context())
    text = paths["runset"].read_text(encoding="utf-8")
    assert "SOURCE CASE YES" in text
    assert "LAYOUT CASE YES" in text

    cfg = cfg.replace('lvs', 'case_sensitive', value=False)
    generate_all(cfg, cfg.context())
    text = paths["runset"].read_text(encoding="utf-8")
    assert "SOURCE CASE YES" not in text
    assert "LAYOUT CASE YES" not in text



def test_xrc_requires_lvs_rule_file(tmp_path: Path) -> None:
    cfg, _ = make_xrc_config(tmp_path)
    cfg = cfg.replace('lvs', 'runset_file', value="")

    with pytest.raises(ValueError, match=r"requires lvs[.]runset_file"):
        generate_all(cfg, cfg.context())



def test_xrc_rejects_missing_lvs_rule_file(tmp_path: Path) -> None:
    cfg, _ = make_xrc_config(tmp_path)
    cfg = cfg.replace('lvs', 'runset_file', value="missing.lvs")

    with pytest.raises(FileNotFoundError, match="Calibre XRC LVS rule file"):
        generate_all(cfg, cfg.context())



def test_xrc_rejects_one_file_used_as_lvs_and_xrc_decks(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    cfg = cfg.replace('lvs', 'runset_file', value=str(paths["corner_dir"] / "xrc.cal"))

    with pytest.raises(ValueError, match="requires different files"):
        generate_all(cfg, cfg.context())



@pytest.mark.parametrize(
    ("top_cell_source", "name_source", "name_option", "output_cell"),
    [
        ("schematic", "layout", "LAYOUTNAMES", "schematic_top"),
        ("layout", "schematic", "SOURCENAMES", "layout_top"),
    ],
)
def test_xrc_name_domain_does_not_rekey_layout_artifacts(
    tmp_path: Path,
    top_cell_source: str,
    name_source: str,
    name_option: str,
    output_cell: str,
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    cfg = cfg.replace('input', 'cdl', 'cell', value="schematic_top")
    cfg = cfg.replace('input', 'gds', 'cell', value="layout_top")
    cfg = cfg.replace('extract', 'top_cell_source', value=top_cell_source)
    cfg = cfg.replace('extract', 'name_source', value=name_source)
    ctx = cfg.context()

    generate_all(cfg, ctx)

    runset = paths["runset"].read_text(encoding="utf-8")
    assert 'SOURCE PRIMARY "schematic_top"' in runset
    assert 'LAYOUT PRIMARY "layout_top"' in runset
    assert f'PEX NETLIST "{ctx.db_dir / f"{output_cell}.dspf"}" DSPF {name_option}' in runset
    assert ctx.svdb_dir.endswith("/db/svdb.layout_top")

    lvs_stage = xrc_stages(cfg)[0]
    svdb = Path(ctx.svdb_dir)
    assert str(svdb / "layout_top.sp") in lvs_stage.command
    assert lvs_stage.expected_paths == (
        svdb / "layout_top.sp",
        svdb / "layout_top.phdb" / "pdb.seg",
        svdb / "layout_top.xdb" / "pdb.seg",
    )
