from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.gen_query import generate_star_query  # noqa: E402
from rcepy.gen_starrc import generate_starrc  # noqa: E402
from rcepy.generators import generate_all  # noqa: E402
from rcepy.starrc_inputs import require_starrc_file  # noqa: E402


def _make_config(tmp_path: Path, tech_dir: Path, corner: str = "RCmax") -> RceConfig:
    return RceConfig(
        raw={
            "run": {"run_dir": str(tmp_path / "run")},
            "input": {
                "type": "CDL+GDS",
                "cdl": {"cell": "top", "file": "top.cdl"},
                "gds": {"cell": "top", "file": "top.gds"},
            },
            "extract": {
                "tool": "StarRC",
                "tech_dir": str(tech_dir),
                "corner": corner,
                "temperature": "25",
            },
        },
        config_path=tmp_path / "rce.toml",
    )


def _make_database_config(
    tmp_path: Path,
    input_type: str,
    *,
    runset: Path | None = None,
) -> RceConfig:
    """Build a StarRC fixture for an externally supplied SVDB or CCI."""
    input_section = {
        "type": input_type,
        input_type.lower(): {
            "cell": "database_top",
            "dir": str(tmp_path / f"input.{input_type.lower()}"),
        },
    }
    tech_dir = tmp_path / "StarRC"
    _write(tech_dir / "RCmax/nxtgrd")
    _write(tech_dir / "RCmax/tran.map")
    lvs: dict[str, object] = {"tool": "Calibre"}
    if runset is not None:
        lvs["runset_file"] = str(runset)
    return RceConfig(
        raw={
            "run": {"run_dir": str(tmp_path / "run")},
            "input": input_section,
            "lvs": lvs,
            "extract": {
                "tool": "StarRC",
                "tech_dir": str(tech_dir),
                "corner": "RCmax",
                "temperature": "25",
                "output_type": "dspf",
            },
        },
        config_path=tmp_path / "rce.toml",
    )


def _write(path: Path, text: str = "test\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_starrc_derives_smic_style_corner_inputs(tmp_path: Path) -> None:
    tech_dir = tmp_path / "StarRC"
    grid = _write(tech_dir / "RCmax/nxtgrd")
    mapping = _write(tech_dir / "RCmax/tran.map")
    cfg = _make_config(tmp_path, tech_dir)

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"TCAD_GRD_FILE: {grid}" in command
    assert f"MAPPING_FILE: {mapping}" in command
    assert "OPERATING_TEMPERATURE: 25" in command
    assert "SELECTED_CORNERS" not in command


def test_starrc_case_matching_defaults_to_strict_and_can_be_disabled(
    tmp_path: Path,
) -> None:
    tech_dir = tmp_path / "StarRC"
    _write(tech_dir / "RCmax/nxtgrd")
    _write(tech_dir / "RCmax/tran.map")
    cfg = _make_config(tmp_path, tech_dir)

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")
    assert "CASE_SENSITIVE: YES" in command

    cfg = cfg.replace('lvs', value={"case_sensitive": False})
    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")
    assert "CASE_SENSITIVE: NO" in command


def test_starrc_accepts_tech_dir_already_ending_in_corner(tmp_path: Path) -> None:
    corner_dir = tmp_path / "StarRC/RCmax"
    grid = _write(corner_dir / "nxtgrd")
    mapping = _write(corner_dir / "map")
    cfg = _make_config(tmp_path, corner_dir, corner="rcMAX")

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"TCAD_GRD_FILE: {grid}" in command
    assert f"MAPPING_FILE: {mapping}" in command
    assert str(corner_dir / "rcMAX") not in command


def test_starrc_explicit_inputs_override_corner_defaults(tmp_path: Path) -> None:
    tech_dir = tmp_path / "StarRC"
    _write(tech_dir / "RCmax/nxtgrd")
    _write(tech_dir / "RCmax/tran.map")
    grid = _write(tmp_path / "overrides/custom.nxtgrd")
    mapping = _write(tmp_path / "overrides/custom.map")
    cfg = _make_config(tmp_path, tech_dir)
    cfg = cfg.replace('extract', 'starrc', value={
        "tcad_grd_file": str(grid),
        "mapping_file": str(mapping),
    })

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"TCAD_GRD_FILE: {grid}" in command
    assert f"MAPPING_FILE: {mapping}" in command


def test_starrc_accepts_unique_named_grid_and_mapping(tmp_path: Path) -> None:
    corner_dir = tmp_path / "StarRC/Typ"
    grid = _write(corner_dir / "process.nxtgrd")
    mapping = _write(corner_dir / "layers.map")
    cfg = _make_config(tmp_path, corner_dir.parent, corner="Typ")

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"TCAD_GRD_FILE: {grid}" in command
    assert f"MAPPING_FILE: {mapping}" in command


def test_starrc_accepts_shared_mapping_at_tech_root(tmp_path: Path) -> None:
    tech_dir = tmp_path / "StarRC"
    grid = _write(tech_dir / "Typ/process.nxtgrd")
    mapping = _write(tech_dir / "MAPPING_FILE")
    cfg = _make_config(tmp_path, tech_dir, corner="Typ")

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"TCAD_GRD_FILE: {grid}" in command
    assert f"MAPPING_FILE: {mapping}" in command


def test_starrc_appends_corner_common_options(tmp_path: Path) -> None:
    tech_dir = tmp_path / "StarRC"
    _write(tech_dir / "Cmax/nxtgrd")
    _write(tech_dir / "Cmax/tran.map")
    common = "* TechReport\nMAGNIFICATION_FACTOR:0.9\nSKIP_PCELLS: *rf*\n"
    _write(tech_dir / "Cmax/common.opt", common)
    cfg = _make_config(tmp_path, tech_dir, corner="Cmax")

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert command.endswith(f"\n{common}")


def test_starrc_rejects_missing_or_ambiguous_derived_inputs(tmp_path: Path) -> None:
    corner_dir = tmp_path / "StarRC/RCmax"
    _write(corner_dir / "nxtgrd")
    cfg = _make_config(tmp_path, corner_dir.parent)

    with pytest.raises(FileNotFoundError, match="StarRC mapping") as exc_info:
        generate_starrc(cfg, cfg.context())
    assert str(corner_dir) in str(exc_info.value)

    _write(corner_dir / "metal.map")
    _write(corner_dir / "via.map")
    with pytest.raises(ValueError, match="Ambiguous StarRC mapping") as exc_info:
        generate_starrc(cfg, cfg.context())
    assert "extract.starrc.mapping_file" in str(exc_info.value)


@pytest.mark.parametrize("input_type", ("SVDB", "CCI"))
def test_starrc_database_inputs_use_selected_rce_runset(
    tmp_path: Path, input_type: str
) -> None:
    runset = _write(tmp_path / "rules/rce_lvs.cal", "// RCE LVS rules\n")
    cfg = _make_database_config(tmp_path, input_type, runset=runset)

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"CALIBRE_RUNSET: {runset}" in command
    query_file = cfg.context().log_dir / "star.query.cmd"
    assert f"CALIBRE_QUERY_FILE: {query_file}" in command
    assert query_file.is_file()


@pytest.mark.parametrize(
    ("configured", "env_name"),
    (
        ("rules/rce_lvs.cal", None),
        ("${RCE_LVS_RULE_ROOT}/rce_lvs.cal", "RCE_LVS_RULE_ROOT"),
    ),
)
def test_starrc_database_runset_uses_shared_path_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    env_name: str | None,
) -> None:
    config_dir = tmp_path / "config"
    runset = _write(config_dir / "rules/rce_lvs.cal", "// RCE LVS rules\n")
    cfg = _make_database_config(config_dir, "SVDB")
    cfg = cfg.replace('lvs', 'runset_file', value=configured)
    if env_name is not None:
        monkeypatch.setenv(env_name, str(runset.parent))

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"CALIBRE_RUNSET: {runset}" in command


def test_starrc_paired_inputs_keep_local_lvs_runset(
    tmp_path: Path,
) -> None:
    tech_dir = tmp_path / "StarRC"
    _write(tech_dir / "RCmax/nxtgrd")
    _write(tech_dir / "RCmax/tran.map")
    cfg = _make_config(tmp_path, tech_dir)

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"CALIBRE_RUNSET: {tmp_path / 'run/log/lvs.cal'}" in command
    assert f"CALIBRE_QUERY_FILE: {tmp_path / 'run/log/star.query.cmd'}" in command


def test_starrc_cci_generates_query_for_external_cci_directory(tmp_path: Path) -> None:
    runset = _write(tmp_path / "rules/rce_lvs.cal", "// RCE LVS rules\n")
    cfg = _make_database_config(tmp_path, "CCI", runset=runset)
    ctx = cfg.context()

    generate_starrc(cfg, ctx)

    query = (ctx.log_dir / "star.query.cmd").read_text(encoding="utf-8")
    prefix = f"{ctx.cci_dir}/{ctx.layout_cell}"
    assert f"GDS WRITE {prefix}.agf" in query
    assert f"CELL EXTENTS WRITE {prefix}.extents" in query


def test_starrc_cci_generate_all_publishes_query_without_rewriting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runset = _write(tmp_path / "rules/rce_lvs.cal", "// RCE LVS rules\n")
    cfg = _make_database_config(tmp_path, "CCI", runset=runset)
    writes = 0

    def count_query_writes(ctx, output_path=None):
        nonlocal writes
        writes += 1
        return generate_star_query(ctx, output_path)

    monkeypatch.setattr("rcepy.generators.generate_star_query", count_query_writes)
    monkeypatch.setattr("rcepy.gen_starrc.generate_star_query", count_query_writes)

    outputs = generate_all(cfg, cfg.context())

    assert outputs["query"] == cfg.context().log_dir / "star.query.cmd"
    assert writes == 1


def test_starrc_cci_preserves_existing_nonempty_query_command(tmp_path: Path) -> None:
    runset = _write(tmp_path / "rules/rce_lvs.cal", "// RCE LVS rules\n")
    cfg = _make_database_config(tmp_path, "CCI", runset=runset)
    query_file = _write(cfg.context().log_dir / "star.query.cmd", "CUSTOM QUERY\n")

    generate_starrc(cfg, cfg.context())

    assert query_file.read_text(encoding="utf-8") == "CUSTOM QUERY\n"


def test_starrc_cci_replaces_empty_query_command(tmp_path: Path) -> None:
    runset = _write(tmp_path / "rules/rce_lvs.cal", "// RCE LVS rules\n")
    cfg = _make_database_config(tmp_path, "CCI", runset=runset)
    query_file = _write(cfg.context().log_dir / "star.query.cmd", "")

    generate_starrc(cfg, cfg.context())

    assert query_file.stat().st_size > 0
    assert query_file.read_text(encoding="utf-8").endswith("TERMINATE\n")


@pytest.mark.parametrize("input_type", ("SVDB", "CCI"))
def test_starrc_database_inputs_require_nonempty_rce_runset(
    tmp_path: Path, input_type: str
) -> None:
    cfg = _make_database_config(tmp_path, input_type)

    with pytest.raises(ValueError, match=r"requires lvs[.]runset_file"):
        generate_starrc(cfg, cfg.context())

    empty = tmp_path / "rules/empty.lvs"
    _write(empty, "")
    cfg = cfg.replace('lvs', 'runset_file', value=str(empty))
    with pytest.raises(FileNotFoundError, match="CALIBRE_RUNSET"):
        generate_starrc(cfg, cfg.context())


@pytest.mark.parametrize("label", ("CALIBRE_RUNSET", "CALIBRE_QUERY_FILE"))
def test_starrc_rejects_unreadable_calibre_inputs_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
) -> None:
    path = _write(tmp_path / "input", "content\n")
    original_open = Path.open

    def deny_target(candidate: Path, *args, **kwargs):
        if candidate == path:
            raise PermissionError("denied")
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_target)

    with pytest.raises(FileNotFoundError, match=label):
        require_starrc_file(path, label, nonempty=True)


def test_starrc_database_stage_policy_keeps_cci_without_query_stage(
    tmp_path: Path,
) -> None:
    runset = _write(tmp_path / "rules/rce_lvs.cal", "// RCE LVS rules\n")
    svdb = _make_database_config(tmp_path / "svdb", "SVDB", runset=runset)
    cci = _make_database_config(tmp_path / "cci", "CCI", runset=runset)

    assert svdb.enabled_stages() == ["query", "extract"]
    assert cci.enabled_stages() == ["extract"]
