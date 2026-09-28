from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from lefpy.config import LefConfig
from lefpy.replay import generate_replay, skill_string


def _config(tmp_path: Path) -> LefConfig:
    cds_lib = tmp_path / "cds.lib"
    options = tmp_path / "rules options.il"
    cds_lib.write_text("DEFINE demo ./demo\n", encoding="utf-8")
    options.write_text("absSkillMode()\n", encoding="utf-8")
    return LefConfig(
        config_path=tmp_path / "lef.toml",
        run_dir=tmp_path / "run dir",
        cds_lib=cds_lib,
        abstract_executable="abstract",
        library='demo"lib',
        cells=("INVX1", "NAND2X1"),
        source_cell_list=None,
        layout_view="layout",
        logical_view="schematic",
        abstract_view="abstract",
        options_file=options,
        bin_name="Core",
        output_lef=tmp_path / "run dir" / "INVX1.lef",
        lef_version="5.8",
        export_geometry=True,
        export_technology=False,
        run_pins=True,
        run_extract=False,
        run_abstract=True,
        bin_options=(
            ("ExtractSig", "true"),
            ("AbstractBlockageCutAroundPin", "Metal1 Metal2"),
        ),
    )


def test_generate_replay_selects_exact_cells_and_overrides_imported_output(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    artifacts = generate_replay(cfg)
    text = artifacts.replay_file.read_text(encoding="utf-8")

    assert 'absSetLibrary("demo\\"lib")' in text
    assert f'absSetOption("ImportOptionsFile" "{cfg.options_file}")' in text
    assert text.index("absImportOptions()") < text.index('absSetOption("DefaultBin" "Core")')
    assert text.index("absImportOptions()") < text.index(
        'absSetBinOption("Core" "ExtractSig" "true")'
    )
    assert 'absSetBinOption("Core" "AbstractBlockageCutAroundPin" "Metal1 Metal2")' in text
    assert text.index('absSetBinOption("Core" "ExtractSig" "true")') < text.index(
        'absSelectCellFrom("INVX1" "INVX1")'
    )
    assert text.index('absSetOption("DefaultBin" "Core")') < text.index(
        'absSelectCellFrom("INVX1" "INVX1")'
    )
    assert text.count('absMoveSelectedCellsToBin("Core")') == 2
    assert 'absSelectCellFrom("NAND2X1" "NAND2X1")' in text
    assert 'absDeselectBinFrom("Ignore" "Ignore")' in text
    assert 'absSelectBinFrom("Core" "Core")' in text
    assert text.rindex('absMoveSelectedCellsToBin("Core")') < text.index(
        'absSelectBinFrom("Core" "Core")'
    )
    assert text.index('absSelectBinFrom("Core" "Core")') < text.index(
        'absSelectCell("INVX1")'
    )
    assert text.index("absImportOptions()") < text.index(
        f'absSetOption("ExportLEFFile" "{cfg.output_lef}")'
    )
    assert 'absSetOption("ExportGeometryLefData" "true")' in text
    assert 'absSetOption("ExportTechLefData" "false")' in text
    assert 'absSelectCell("INVX1")' in text
    assert 'absSelectCell("NAND2X1")' in text
    assert "absPins()" in text
    assert "absExtract()" not in text
    assert "absAbstract()" in text
    assert text.endswith("absExit()\n")
    assert artifacts.cell_list_file.read_text(encoding="utf-8") == (
        "INVX1\nNAND2X1\n"
    )
    assert artifacts.lefout_log == cfg.run_dir / "lefout.log"


def test_skill_string_rejects_replay_line_injection() -> None:
    with pytest.raises(ValueError, match="control characters"):
        skill_string('INVX1\nabsExit()')


def test_generate_replay_does_not_invalidate_abstracts_for_export_only(
    tmp_path: Path,
) -> None:
    cfg = replace(
        _config(tmp_path),
        run_pins=False,
        run_extract=False,
        run_abstract=False,
    )

    artifacts = generate_replay(cfg)
    text = artifacts.replay_file.read_text(encoding="utf-8")

    assert "absImportOptions()" not in text
    assert 'absSetOption("ImportOptionsFile"' not in text
    assert "absPins()" not in text
    assert "absExtract()" not in text
    assert "absAbstract()" not in text
    assert "absSetBinOption" not in text
    assert "absExportLEF()" in text
