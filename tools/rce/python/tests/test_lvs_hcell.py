from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
CAD_ROOT = Path(__file__).resolve().parents[3]
LVS_PYTHON_ROOT = CAD_ROOT / "lvs" / "python"
for root in (PYTHON_ROOT, LVS_PYTHON_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from lvspy.runner import LvsRunner, eda_env as lvs_eda_env, open_rve  # noqa: E402
from rcepy.config import RceConfig  # noqa: E402
from rcepy.gen_lvs import generate_lvs  # noqa: E402
from rcepy.generators import generate_all  # noqa: E402
from rcepy.runner import RceRunner  # noqa: E402
from xrc_test_support import make_xrc_config, xrc_stages  # noqa: E402


RUNNER_TYPES = (RceRunner, LvsRunner)


def make_lvs_config(tmp_path: Path) -> tuple[RceConfig, dict[str, Path]]:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    run_dir = tmp_path / "run"
    cfg = RceConfig(
        raw={
            "run": {"run_dir": str(run_dir), "cds_lib": ""},
            "input": {
                "type": "CDL+GDS",
                "cdl": {"file": "top.cdl", "cell": "top"},
                "gds": {"file": "top.gds", "cell": "top"},
            },
            "lvs": {"tool": "Calibre"},
            "extract": {"tool": "QRC"},
            "runtime": {"lvs_cpus": "2", "ext_cpus": "1"},
        },
        config_path=config_dir / "lvs.toml",
    )
    return cfg, {
        "config_dir": config_dir,
        "run_dir": run_dir,
        "runset": run_dir / "log" / "lvs.cal",
    }


def lvs_command(runner: RceRunner | LvsRunner) -> list[str]:
    return next(stage.command for stage in runner._stages() if stage.name == "lvs")


def test_lvs_case_matching_defaults_to_strict_and_can_be_disabled(
    tmp_path: Path,
) -> None:
    cfg, paths = make_lvs_config(tmp_path)
    generate_lvs(cfg, cfg.context())
    runset = paths["runset"].read_text(encoding="utf-8")
    assert "SOURCE CASE YES" in runset
    assert "LAYOUT CASE YES" in runset

    cfg = cfg.replace('lvs', 'case_sensitive', value=False)
    generate_lvs(cfg, cfg.context())
    runset = paths["runset"].read_text(encoding="utf-8")
    assert "SOURCE CASE YES" not in runset
    assert "LAYOUT CASE YES" not in runset


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, "QUERY CCI PINLOC"),
        (["SI", "RECON", "IXF NXF SLPH"], "QUERY SI RECON IXF NXF SLPH"),
        ([], "QUERY"),
    ],
)
def test_lvs_svdb_query_tokens_render_selected_values(
    tmp_path: Path, configured: list[str] | None, expected: str
) -> None:
    cfg, paths = make_lvs_config(tmp_path)
    if configured is not None:
        cfg = cfg.replace('lvs', 'svdb_query', value=configured)

    generate_lvs(cfg, cfg.context(), standalone=True)

    text = paths["runset"].read_text(encoding="utf-8")
    assert f'MASK SVDB DIRECTORY "{cfg.context().svdb_dir}" {expected}' in text


def test_lvs_svdb_query_tokens_reject_unknown_values(tmp_path: Path) -> None:
    cfg, _ = make_lvs_config(tmp_path)
    cfg = cfg.replace('lvs', 'svdb_query', value=["NOT_A_QUERY"])

    with pytest.raises(ValueError, match="Unsupported lvs.svdb_query"):
        generate_lvs(cfg, cfg.context(), standalone=True)


@pytest.mark.parametrize("configured", [None, [], ["CCI", "PINLOC"]])
def test_rce_lvs_always_requests_only_cci(tmp_path: Path, configured) -> None:
    cfg, _ = make_lvs_config(tmp_path)
    if configured is not None:
        cfg = cfg.replace('lvs', 'svdb_query', value=configured)

    text = generate_lvs(cfg, cfg.context()).read_text(encoding="utf-8")

    assert next(line for line in text.splitlines() if line.startswith("MASK SVDB")) == (
        f'MASK SVDB DIRECTORY "{cfg.context().svdb_dir}" QUERY CCI'
    )


def test_standalone_lvs_runner_preserves_pinloc_selection(tmp_path: Path) -> None:
    cfg, _ = make_lvs_config(tmp_path)
    cfg = cfg.replace('lvs', 'svdb_query', value=["CCI", "PINLOC"])

    text = LvsRunner(cfg)._generate()["lvs"].read_text(encoding="utf-8")

    assert next(line for line in text.splitlines() if line.startswith("MASK SVDB")) == (
        f'MASK SVDB DIRECTORY "{cfg.context().svdb_dir}" QUERY CCI PINLOC'
    )


def test_lvs_appends_enabled_custom_svrf_verbatim(tmp_path: Path) -> None:
    cfg, paths = make_lvs_config(tmp_path)
    custom = (
        'LVS FILTER UNUSED OPTION AB \\ path\n'
        '// keep "quoted" value\n  LVS REPORT OPTION S  '
    )
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{
            "custom_svrf_enable": True,
            "custom_svrf_command": custom,
        }})

    generate_lvs(cfg, cfg.context())

    text = paths["runset"].read_text(encoding="utf-8")
    assert custom in text
    assert text.index(custom) < text.index("LVS EXECUTE ERC YES")


def test_lvs_places_erc_and_runset_include_at_the_end(tmp_path: Path) -> None:
    cfg, paths = make_lvs_config(tmp_path)
    runset = paths["config_dir"] / "rules" / "foundry.lvs"
    runset.parent.mkdir()
    runset.write_text("// foundry LVS rules\n", encoding="utf-8")
    cfg = cfg.replace('lvs', 'runset_file', value=str(runset))

    generate_lvs(cfg, cfg.context())

    lines = paths["runset"].read_text(encoding="utf-8").splitlines()
    assert lines[-1] == f'INCLUDE "{runset}"'
    assert lines[-2] == "DRC ICSTATION YES"
    erc = [
        "LVS EXECUTE ERC YES",
        f'ERC RESULTS DATABASE "{paths["run_dir"] / "log" / "erc.results"}"',
        "ERC CELL NAME YES CELL SPACE XFORM",
    ]
    erc_start = lines.index(erc[0])
    assert lines[erc_start : erc_start + len(erc)] == erc
    assert erc_start < len(lines) - 2


def test_lvs_uses_drc_icstation_as_last_line_without_runset(tmp_path: Path) -> None:
    cfg, paths = make_lvs_config(tmp_path)

    generate_lvs(cfg, cfg.context())

    lines = paths["runset"].read_text(encoding="utf-8").splitlines()
    assert lines[-1] == "DRC ICSTATION YES"


def test_lvs_ignores_disabled_custom_svrf(tmp_path: Path) -> None:
    cfg, paths = make_lvs_config(tmp_path)
    custom = "// CUSTOM SVRF MUST NOT BE WRITTEN"
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{
            "custom_svrf_enable": False,
            "custom_svrf_command": custom,
        }})

    generate_lvs(cfg, cfg.context())

    text = paths["runset"].read_text(encoding="utf-8")
    assert custom not in text


@pytest.mark.parametrize("runner_type", RUNNER_TYPES)
def test_generic_lvs_enabled_hcell_has_exact_command_arguments(
    tmp_path: Path, runner_type: type[RceRunner] | type[LvsRunner]
) -> None:
    cfg, paths = make_lvs_config(tmp_path)
    hcell = paths["config_dir"] / "lists" / "custom.hcells"
    hcell.parent.mkdir()
    hcell.write_text("macro macro\n", encoding="utf-8")
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{"hcell_enable": True, "hcell_file": "lists/custom.hcells"}})

    assert lvs_command(runner_type(cfg)) == [
        "calibre",
        "-lvs",
        "-turbo",
        "2",
        "-hier",
        "-hcell",
        str(hcell),
        str(paths["runset"]),
    ]


def test_standalone_lvs_flat_mode_omits_hierarchical_turbo_options(
    tmp_path: Path,
) -> None:
    cfg, paths = make_lvs_config(tmp_path)
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{
            "run_mode": "Flat",
            "hcell_enable": True,
            "hcell_file": "missing-is-ignored-in-flat-mode.hcells",
        }})

    assert lvs_command(LvsRunner(cfg)) == [
        "calibre",
        "-lvs",
        str(paths["runset"]),
    ]


def test_standalone_lvs_rejects_invalid_run_mode(tmp_path: Path) -> None:
    cfg, _ = make_lvs_config(tmp_path)
    cfg = cfg.replace('lvs', 'run_mode', value="parallel")

    with pytest.raises(ValueError, match="lvs.run_mode.*Hier.*Flat"):
        LvsRunner(cfg)._stages()


def test_standalone_lvs_open_rve_launches_svdb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_lvs_config(tmp_path)
    svdb = paths["run_dir"] / "db" / "svdb.top"
    svdb.mkdir(parents=True)
    launched: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> object:
        launched.update(command=command, kwargs=kwargs)
        return object()

    monkeypatch.setattr("lvspy.runner.subprocess.Popen", fake_popen)

    assert open_rve(cfg) == 0
    assert launched["command"] == ["calibre", "-rve", str(svdb)]
    kwargs = launched["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == str(paths["run_dir"])
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True


def test_lvs_eda_environment_restores_empty_original_library_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LD_LIBRARY_PATH", "/cad/python/lib")
    monkeypatch.setenv("LVS_ORIG_LD_LIBRARY_PATH", "")

    assert lvs_eda_env()["LD_LIBRARY_PATH"] == ""


@pytest.mark.parametrize("runner_type", RUNNER_TYPES)
@pytest.mark.parametrize(
    "lvs_options",
    [{}, {"hcell_enable": False, "hcell_file": "$UNDEFINED_HCELL/file"}],
)
def test_generic_lvs_missing_or_disabled_hcell_omits_argument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_type: type[RceRunner] | type[LvsRunner],
    lvs_options: dict[str, object],
) -> None:
    monkeypatch.delenv("UNDEFINED_HCELL", raising=False)
    cfg, _ = make_lvs_config(tmp_path)
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **lvs_options})

    assert "-hcell" not in lvs_command(runner_type(cfg))


@pytest.mark.parametrize("runner_type", RUNNER_TYPES)
@pytest.mark.parametrize(
    ("hcell_file", "error", "message"),
    [
        ("", ValueError, "lvs.hcell_file is empty"),
        ("missing.hcells", FileNotFoundError, "Cannot access Calibre LVS hcell file"),
    ],
)
def test_generic_lvs_enabled_hcell_rejects_invalid_file(
    tmp_path: Path,
    runner_type: type[RceRunner] | type[LvsRunner],
    hcell_file: str,
    error: type[Exception],
    message: str,
) -> None:
    cfg, _ = make_lvs_config(tmp_path)
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{"hcell_enable": True, "hcell_file": hcell_file}})

    with pytest.raises(error, match=message):
        lvs_command(runner_type(cfg))


def test_lvs_hcell_path_expands_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = make_lvs_config(tmp_path)
    hcell_root = tmp_path / "project"
    hcell_root.mkdir()
    hcell = hcell_root / "project.hcells"
    hcell.write_text("macro macro\n", encoding="utf-8")
    monkeypatch.setenv("HCELL_ROOT", str(hcell_root))
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{"hcell_enable": True, "hcell_file": "$HCELL_ROOT/project.hcells"}})

    command = lvs_command(RceRunner(cfg))

    assert command[command.index("-hcell") + 1] == str(hcell)


def test_user_hcell_file_is_not_included_as_svrf_selection_file(
    tmp_path: Path,
) -> None:
    cfg, paths = make_lvs_config(tmp_path)
    user_hcell = paths["config_dir"] / "user.hcells"
    user_hcell.write_text("layout_macro source_macro\n", encoding="utf-8")
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{"hcell_enable": True, "hcell_file": str(user_hcell)}})
    cfg = cfg.replace('selection', value={"cell_enable": True, "cells": ["block"]})

    runset = generate_lvs(cfg, cfg.context())
    text = runset.read_text(encoding="utf-8")

    assert str(user_hcell) not in text
    assert f'INCLUDE "{paths["run_dir"] / "log" / "hcells"}"' in text
    assert (paths["run_dir"] / "log" / "hcells").read_text(
        encoding="utf-8"
    ) == "HCELL block block\n"


def test_standalone_lvs_validates_hcell_before_backing_up_run_data(
    tmp_path: Path,
) -> None:
    cfg, paths = make_lvs_config(tmp_path)
    log_dir = paths["run_dir"] / "log"
    db_dir = paths["run_dir"] / "db"
    log_dir.mkdir(parents=True)
    db_dir.mkdir()
    marker = log_dir / "keep.log"
    marker.write_text("keep\n", encoding="utf-8")
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{"hcell_enable": True, "hcell_file": "missing.hcells"}})

    with pytest.raises(FileNotFoundError, match="Calibre LVS hcell file"):
        LvsRunner(cfg, generate_only=True).run()

    assert marker.is_file()
    assert not list(paths["run_dir"].glob("log.*"))
    assert db_dir.is_dir()


def test_standalone_lvs_collects_run_root_files_in_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_lvs_config(tmp_path)
    run_dir = paths["run_dir"]
    log_dir = run_dir / "log"
    run_dir.mkdir()
    log_dir.mkdir()
    (log_dir / "old.log").write_text("old log\n", encoding="utf-8")
    (run_dir / "erc.db").write_text("old db\n", encoding="utf-8")
    (run_dir / "erc.rep").write_text("old report\n", encoding="utf-8")
    cfg = type(cfg)(cfg.to_dict(), run_dir / "lvs.toml")
    cfg.config_path.write_text("[run]\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calibre = bin_dir / "calibre"
    calibre.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "printf 'new db\\n' > erc.db\n"
        "printf 'new report\\n' > erc.rep\n"
        "printf 'LVS completed. CORRECT.\\n'\n",
        encoding="utf-8",
    )
    calibre.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path('/usr/bin')}:{Path('/bin')}")
    monkeypatch.delenv("LVS_BACKUP_DONE", raising=False)

    assert LvsRunner(cfg).run() == 0

    assert (run_dir / "lvs.toml").read_text(encoding="utf-8") == "[run]\n"
    assert not (run_dir / "erc.db").exists()
    assert not (run_dir / "erc.rep").exists()
    assert cfg.config_path == run_dir / "lvs.toml"
    assert (log_dir / "lvs.toml").is_file()
    assert (log_dir / "erc.db").read_text(encoding="utf-8") == "new db\n"
    assert (log_dir / "erc.rep").read_text(encoding="utf-8") == "new report\n"

    archived_log = next(run_dir.glob("log.*"))
    assert (archived_log / "old.log").is_file()
    assert (archived_log / "erc.db").read_text(encoding="utf-8") == "old db\n"
    assert (archived_log / "erc.rep").read_text(encoding="utf-8") == "old report\n"


def test_standalone_lvs_collects_erc_files_after_calibre_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_lvs_config(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calibre = bin_dir / "calibre"
    calibre.write_text(
        "#!/bin/sh\n"
        "printf 'failed db\\n' > erc.db\n"
        "printf 'failed report\\n' > erc.rep\n"
        "exit 17\n",
        encoding="utf-8",
    )
    calibre.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path('/usr/bin')}:{Path('/bin')}")

    with pytest.raises(RuntimeError, match="lvs failed with exit code 17"):
        LvsRunner(cfg).run()

    run_dir = paths["run_dir"]
    assert not (run_dir / "erc.db").exists()
    assert not (run_dir / "erc.rep").exists()
    assert (run_dir / "log" / "erc.db").read_text(encoding="utf-8") == "failed db\n"
    assert (run_dir / "log" / "erc.rep").read_text(encoding="utf-8") == "failed report\n"


def test_xrc_canonical_hcell_overrides_legacy_alias(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    canonical = tmp_path / "canonical.hcells"
    legacy = paths["corner_dir"] / "legacy.hcells"
    canonical.write_text("canonical canonical\n", encoding="utf-8")
    legacy.write_text("legacy legacy\n", encoding="utf-8")
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{"hcell_enable": True, "hcell_file": canonical.name}})
    cfg = cfg.replace('extract', 'xrc', value={"hcell_file": legacy.name})

    command = xrc_stages(cfg)[0].command

    assert command[command.index("-hcell") + 1] == str(canonical)
    assert str(legacy) not in command


def test_xrc_disabled_hcell_ignores_configured_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = make_xrc_config(tmp_path)
    monkeypatch.delenv("UNDEFINED_HCELL", raising=False)
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{"hcell_enable": False, "hcell_file": "$UNDEFINED_HCELL/canonical"}})
    cfg = cfg.replace('extract', 'xrc', value={"hcell_file": "$UNDEFINED_HCELL/legacy"})

    generate_all(cfg, cfg.context())

    assert "-hcell" not in xrc_stages(cfg)[0].command


def test_xrc_enabled_empty_hcell_uses_corner_fallback(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{"hcell_enable": True, "hcell_file": ""}})

    command = xrc_stages(cfg)[0].command

    assert command[command.index("-hcell") + 1] == str(
        paths["corner_dir"] / "hcell_list"
    )


def test_xrc_missing_corner_hcell_reports_resolved_path(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    hcell = paths["corner_dir"] / "hcell_list"
    hcell.unlink()

    with pytest.raises(FileNotFoundError) as exc_info:
        xrc_stages(cfg)

    assert str(hcell) in str(exc_info.value)
