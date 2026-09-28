from __future__ import annotations

from pathlib import Path

import pytest

from lefpy.config import load_config


def _write_config(
    tmp_path: Path,
    *,
    output: str = "macro.lef",
    input_cells: str = 'cell = "INVX1"',
    include_options: bool = True,
    run_extra: str = "",
    steps: str = "",
    abstract_extra: str = "",
) -> Path:
    cds_lib = tmp_path / "cds.lib"
    options = tmp_path / "abstract.options"
    cds_lib.write_text("DEFINE demo ./demo\n", encoding="utf-8")
    options.write_text("absSkillMode()\n", encoding="utf-8")
    options_line = f'options_file = "{options.name}"' if include_options else ""
    config = tmp_path / "lef.toml"
    config.write_text(
        f"""
[run]
run_dir = "run"
cds_lib = "{cds_lib.name}"
{run_extra}

[input]
library = "demo"
{input_cells}

[abstract]
{options_line}
{abstract_extra}

{steps}
[output]
lef_file = "{output}"
technology = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config


def test_load_config_resolves_paths_and_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXT_LSF_QUEUE", "legacy")
    cfg = load_config(_write_config(tmp_path))

    assert cfg.run_dir == (tmp_path / "run").resolve()
    assert cfg.output_lef == (tmp_path / "run" / "macro.lef").resolve()
    assert cfg.cells == ("INVX1",)
    assert cfg.source_cell_list is None
    assert cfg.layout_view == "layout"
    assert cfg.logical_view == "schematic"
    assert cfg.abstract_view == "abstract"
    assert cfg.bin_name == "Core"
    assert cfg.lef_version == "5.8"
    assert cfg.export_geometry is True
    assert cfg.export_technology is True
    assert cfg.run_pins is True
    assert cfg.run_extract is True
    assert cfg.run_abstract is True
    assert cfg.bin_options == ()
    assert cfg.run_type == "Current Host"
    assert cfg.queue_name == ""
    assert cfg.cpus == "1"


def test_load_config_preserves_hardware_configuration(tmp_path: Path) -> None:
    cfg = load_config(
        _write_config(
            tmp_path,
            run_extra=(
                'run_type = "LSF Farm"\nqueue_name = "normal"\n'
                'server_name = "node08"\ncpus = "8"'
            ),
        )
    )

    assert cfg.run_type == "LSF Farm"
    assert cfg.queue_name == "normal"
    assert cfg.server_name == "node08"
    assert cfg.cpus == "8"


def test_load_config_rejects_legacy_local_host_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported run.run_type"):
        load_config(_write_config(tmp_path, run_extra='run_type = "Local Host"'))


def test_load_config_accepts_cells_array_and_step_switches(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        input_cells='cells = ["INVX1", "NAND2X1"]',
        steps="[steps]\npins = false\nextract = true\nabstract = false\n",
    )

    cfg = load_config(config)

    assert cfg.cells == ("INVX1", "NAND2X1")
    assert (cfg.run_pins, cfg.run_extract, cfg.run_abstract) == (False, True, False)


def test_load_config_reads_normalized_cell_list(tmp_path: Path) -> None:
    cell_list = tmp_path / "core.cells"
    cell_list.write_text(" INVX1 \n\nNAND2X1\n", encoding="utf-8")
    config = _write_config(tmp_path, input_cells='cell_list_file = "core.cells"')

    cfg = load_config(config)

    assert cfg.cells == ("INVX1", "NAND2X1")
    assert cfg.source_cell_list == cell_list.resolve()


def test_load_config_preserves_executable_symlink(tmp_path: Path) -> None:
    target = tmp_path / "wrapper"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    link = tmp_path / "abstract"
    link.symlink_to(target)
    config = _write_config(
        tmp_path,
        run_extra=f'executable = "{link}"',
    )

    cfg = load_config(config)

    assert cfg.abstract_executable == str(link)


def test_load_config_requires_exactly_one_cell_source(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        input_cells='cell = "INVX1"\ncells = ["NAND2X1"]',
    )

    with pytest.raises(ValueError, match="Exactly one"):
        load_config(config)


def test_load_config_rejects_duplicate_cells(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        input_cells='cells = ["INVX1", "INVX1"]',
    )

    with pytest.raises(ValueError, match="Duplicate cell"):
        load_config(config)


def test_load_config_allows_export_only_without_options(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        include_options=False,
        steps="[steps]\npins = false\nextract = false\nabstract = false\n",
    )

    cfg = load_config(config)

    assert cfg.options_file is None


def test_load_config_requires_options_for_generation(tmp_path: Path) -> None:
    config = _write_config(tmp_path, include_options=False)

    with pytest.raises(ValueError, match="options_file is required"):
        load_config(config)


def test_load_config_normalizes_bin_option_scalars(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        abstract_extra="""
[abstract.bin_options]
ExtractSig = true
ExtractNumLevelsSig = 20
AbstractBlockageCutAroundPin = ""
PinsBoundaryCreate = "as needed"
""".strip(),
    )

    cfg = load_config(config)

    assert cfg.bin_options == (
        ("ExtractSig", "true"),
        ("ExtractNumLevelsSig", "20"),
        ("AbstractBlockageCutAroundPin", ""),
        ("PinsBoundaryCreate", "as needed"),
    )


@pytest.mark.parametrize(
    ("option", "value", "message"),
    (
        ("ExtractSig", '"sometimes"', "Invalid value for ExtractSig"),
        ("PinsBoundaryCreate", '"when useful"', "PinsBoundaryCreate"),
        ("Bad-Name", '"x"', "Invalid Abstract Generator bin option name"),
        ("ExtractLayersSig", '["Metal1"]', "must be a string, boolean, or number"),
    ),
)
def test_load_config_rejects_invalid_bin_options(
    tmp_path: Path, option: str, value: str, message: str
) -> None:
    key = f'"{option}"' if "-" in option else option
    config = _write_config(
        tmp_path,
        abstract_extra=f"[abstract.bin_options]\n{key} = {value}",
    )

    with pytest.raises(ValueError, match=message):
        load_config(config)


def test_load_config_rejects_undefined_environment_variable(tmp_path: Path) -> None:
    config = _write_config(tmp_path, output="${LEF_TEST_UNDEFINED}/macro.lef")

    with pytest.raises(ValueError, match="LEF_TEST_UNDEFINED"):
        load_config(config)


def test_load_config_rejects_empty_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEF_TEST_EMPTY", "")
    config = _write_config(tmp_path, output="${LEF_TEST_EMPTY}/macro.lef")

    with pytest.raises(ValueError, match="LEF_TEST_EMPTY"):
        load_config(config)


def test_ic618_options_example_is_ic231_compatible() -> None:
    example = Path(__file__).resolve().parents[1] / "examples" / (
        "ic618_rak_abstract_ic231.options"
    )
    content = example.read_text(encoding="utf-8")

    assert 'absSetOption( "ImportLogicalType" "LIB")' in content
    assert "ImportCTLFFiles" not in content
    assert content.count("absSetBinOption(") == 29
