from __future__ import annotations

from pathlib import Path

import pytest

from lefpy.config import LefConfig
from lefpy.runner import LefRunner, abstract_errors, eda_env


def _config(tmp_path: Path, executable: Path) -> LefConfig:
    cds_lib = tmp_path / "cds.lib"
    options = tmp_path / "abstract.options"
    cds_lib.write_text("DEFINE demo ./demo\n", encoding="utf-8")
    options.write_text("absSkillMode()\n", encoding="utf-8")
    return LefConfig(
        config_path=tmp_path / "lef.toml",
        run_dir=tmp_path / "run",
        cds_lib=cds_lib,
        abstract_executable=str(executable),
        library="demo",
        cells=("INVX1",),
        source_cell_list=None,
        layout_view="layout",
        logical_view="schematic",
        abstract_view="abstract",
        options_file=options,
        bin_name="Core",
        output_lef=tmp_path / "run" / "INVX1.lef",
        lef_version="5.8",
        export_geometry=True,
        export_technology=False,
        run_pins=True,
        run_extract=True,
        run_abstract=True,
    )


def _fake_abstract(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake abstract"
    executable.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    executable.chmod(0o755)
    return executable


def _valid_lef(*cells: str) -> str:
    macros = "".join(f"MACRO {cell}\nEND {cell}\n" for cell in cells)
    return f"VERSION 5.8 ;\n{macros}END LIBRARY\n"


def _set_paths(
    monkeypatch: pytest.MonkeyPatch,
    cfg: LefConfig,
    *,
    lef: str | None = None,
) -> None:
    monkeypatch.setenv("FAKE_LOG", str(cfg.run_dir / "log" / "abstract.log"))
    monkeypatch.setenv("FAKE_LEFOUT", str(cfg.run_dir / "lefout.log"))
    monkeypatch.setenv("FAKE_OUTPUT", str(cfg.output_lef))
    monkeypatch.setenv("FAKE_LEF", lef or _valid_lef(*cfg.cells))


def test_runner_launches_nogui_and_backs_up_previous_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args_file = tmp_path / "args.txt"
    executable = _fake_abstract(
        tmp_path,
        'printf "%s\\n" "$@" > "$FAKE_ARGS"\n'
        'printf "INFO (ABS-1): completed\\n" > "$FAKE_LOG"\n'
        'printf "lefout translation completed (errors: 0, warnings: 0).\\n" '
        '> "$FAKE_LEFOUT"\n'
        'printf "%s" "$FAKE_LEF" > "$FAKE_OUTPUT"\n',
    )
    cfg = _config(tmp_path, executable)
    cfg.run_dir.mkdir()
    cfg.output_lef.write_text("old\n", encoding="utf-8")
    (cfg.run_dir / "lefout.log").write_text("old log\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_ARGS", str(args_file))
    _set_paths(monkeypatch, cfg)

    assert LefRunner(cfg).run() == 0

    assert cfg.output_lef.read_text(encoding="utf-8").startswith("VERSION 5.8")
    backups = list(tmp_path.glob("run.*"))
    assert len(backups) == 1
    assert (backups[0] / "INVX1.lef").read_text(encoding="utf-8") == "old\n"
    assert (backups[0] / "lefout.log").read_text(encoding="utf-8") == "old log\n"
    assert not cfg.output_lef.with_name("INVX1.lef.prev").exists()
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args[0] == "-nogui"
    assert args[1:3] == ["-replay", str(cfg.run_dir / "lef.replay.il")]
    assert "-cdslib" in args


def test_runner_fails_when_abstract_does_not_create_lef(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_abstract(
        tmp_path,
        'printf "INFO (ABS-1): completed\\n" > "$FAKE_LOG"\n'
        'printf "lefout translation completed (errors: 0, warnings: 0).\\n" '
        '> "$FAKE_LEFOUT"\n',
    )
    cfg = _config(tmp_path, executable)
    _set_paths(monkeypatch, cfg)

    with pytest.raises(RuntimeError, match="did not create LEF output"):
        LefRunner(cfg).run()

    marker = cfg.run_dir / "log" / "exit-abnormally"
    assert "did not create LEF output" in marker.read_text(encoding="utf-8")


def test_generate_only_does_not_require_installed_abstract(tmp_path: Path) -> None:
    cfg = _config(tmp_path, tmp_path / "not-installed-abstract")

    assert LefRunner(cfg, generate_only=True).run() == 0
    assert (cfg.run_dir / "lef.replay.il").is_file()


def test_runner_moves_the_complete_previous_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_abstract(
        tmp_path,
        'printf "INFO (ABS-1): completed\\n" > "$FAKE_LOG"\n'
        'printf "lefout translation completed (errors: 0, warnings: 0).\\n" '
        '> "$FAKE_LEFOUT"\n'
        'printf "%s" "$FAKE_LEF" > "$FAKE_OUTPUT"\n',
    )
    cfg = _config(tmp_path, executable)
    cfg.run_dir.mkdir(parents=True)
    stale = cfg.run_dir / ".abstract" / "demo" / "stale.options"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")
    (cfg.run_dir / "lef.replay.il").write_text("stale replay\n", encoding="utf-8")
    _set_paths(monkeypatch, cfg)

    assert LefRunner(cfg).run() == 0

    backups = list(tmp_path.glob("run.*"))
    assert len(backups) == 1
    assert (backups[0] / ".abstract" / "demo" / "stale.options").is_file()
    assert not stale.exists()
    assert (cfg.run_dir / "lef.replay.il").read_text(encoding="utf-8").startswith(
        "absSkillMode()"
    )


def test_runner_restores_config_file_from_the_backed_up_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_abstract(
        tmp_path,
        'printf "INFO (ABS-1): completed\\n" > "$FAKE_LOG"\n'
        'printf "lefout translation completed (errors: 0, warnings: 0).\\n" '
        '> "$FAKE_LEFOUT"\n'
        'printf "%s" "$FAKE_LEF" > "$FAKE_OUTPUT"\n',
    )
    cfg = _config(tmp_path, executable)
    cfg.run_dir.mkdir(parents=True)
    run_config = cfg.run_dir / "lef.toml"
    run_config.write_text("current config\n", encoding="utf-8")
    object.__setattr__(cfg, "config_path", run_config)
    _set_paths(monkeypatch, cfg)

    assert LefRunner(cfg).run() == 0

    assert run_config.read_text(encoding="utf-8") == "current config\n"
    backups = list(tmp_path.glob("run.*"))
    assert len(backups) == 1
    assert (backups[0] / "lef.toml").read_text(encoding="utf-8") == "current config\n"


def test_generate_only_keeps_existing_run_directory_in_place(tmp_path: Path) -> None:
    cfg = _config(tmp_path, tmp_path / "not-installed-abstract")
    cfg.run_dir.mkdir(parents=True)
    stale = cfg.run_dir / ".abstract" / "demo" / "stale.options"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")

    assert LefRunner(cfg, generate_only=True).run() == 0

    assert stale.is_file()
    assert list(tmp_path.glob("run.*")) == []


def test_runner_skips_backup_when_frontend_already_did_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_abstract(
        tmp_path,
        'printf "INFO (ABS-1): completed\\n" > "$FAKE_LOG"\n'
        'printf "lefout translation completed (errors: 0, warnings: 0).\\n" '
        '> "$FAKE_LEFOUT"\n'
        'printf "%s" "$FAKE_LEF" > "$FAKE_OUTPUT"\n',
    )
    cfg = _config(tmp_path, executable)
    cfg.run_dir.mkdir(parents=True)
    config = cfg.run_dir / "lef.toml"
    config.write_text("frontend config\n", encoding="utf-8")
    monkeypatch.setenv("LEF_BACKUP_DONE", "1")
    _set_paths(monkeypatch, cfg)

    assert LefRunner(cfg).run() == 0

    assert config.read_text(encoding="utf-8") == "frontend config\n"
    assert list(tmp_path.glob("run.*")) == []


def test_runner_rejects_zero_exit_when_abstract_log_contains_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_abstract(
        tmp_path,
        'printf "*Error* (ABS-11911): invalid option\\n" > "$FAKE_LOG"\n'
        'printf "%s" "$FAKE_LEF" > "$FAKE_OUTPUT"\n',
    )
    cfg = _config(tmp_path, executable)
    _set_paths(monkeypatch, cfg)

    with pytest.raises(RuntimeError, match="reported 1 error"):
        LefRunner(cfg).run()

    marker = cfg.run_dir / "log" / "exit-abnormally"
    assert "ABS-11911" in marker.read_text(encoding="utf-8")


def test_runner_rejects_lefout_error_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_abstract(
        tmp_path,
        'printf "INFO (ABS-1): completed\\n" > "$FAKE_LOG"\n'
        'printf "lefout translation completed (errors: 2, warnings: 0).\\n" '
        '> "$FAKE_LEFOUT"\n'
        'printf "%s" "$FAKE_LEF" > "$FAKE_OUTPUT"\n',
    )
    cfg = _config(tmp_path, executable)
    _set_paths(monkeypatch, cfg)

    with pytest.raises(RuntimeError, match="LEF exporter reported 1 error"):
        LefRunner(cfg).run()


def test_runner_rejects_unexpected_macro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_abstract(
        tmp_path,
        'printf "INFO (ABS-1): completed\\n" > "$FAKE_LOG"\n'
        'printf "lefout translation completed (errors: 0, warnings: 0).\\n" '
        '> "$FAKE_LEFOUT"\n'
        'printf "%s" "$FAKE_LEF" > "$FAKE_OUTPUT"\n',
    )
    cfg = _config(tmp_path, executable)
    _set_paths(monkeypatch, cfg, lef=_valid_lef("NAND2X1"))

    with pytest.raises(RuntimeError, match="LEF MACRO set mismatch"):
        LefRunner(cfg).run()


def test_abstract_errors_ignores_optional_aivivc_loader_gap(tmp_path: Path) -> None:
    log = tmp_path / "abstract.log"
    log.write_text(
        "\n".join(
            (
                "Warning (gdmiExecGetConfig): Got stderr from aivivcgdmconfig command.",
                " Error (gdmForkExec): Unable to run the aivivcgdmconfig command: No such file or directory",
                "Error (gdmLoadSharedLib): GDM is unable to load the shared library \"libgdmaivivc_sh.so\".",
                "Error (gdmLoadSharedLib): The message returned from the system is: libgdmaivivc_sh.so: cannot open shared object file: No such file or directory",
                "Error (gdmLoadSharedLib): Contact the owner of the library to resolve the problem",
                "Error (gdmiLoadDMLibrary): GDM failed to load the DM library './libgdmaivivc_sh.so' for the DM system 'aivivc'",
                "Error (gdmImportDMSystem): DM system 'aivivc' as specified in file 'cdsinfo.tag' is not available.",
                "*Error* (ABS-11911): a real Abstract Generator failure",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    assert abstract_errors(log) == [
        "*Error* (ABS-11911): a real Abstract Generator failure"
    ]


def test_abstract_errors_keeps_unrelated_gdm_error(tmp_path: Path) -> None:
    log = tmp_path / "abstract.log"
    log.write_text(
        "Error (gdmImportDMSystem): unrelated DM failure\n", encoding="utf-8"
    )

    assert abstract_errors(log) == [
        "Error (gdmImportDMSystem): unrelated DM failure"
    ]


def test_eda_environment_restores_original_temp_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch_temp = tmp_path / "launch" / ".cad"
    changed = tmp_path / "run"
    changed.mkdir()
    monkeypatch.chdir(changed)
    monkeypatch.setenv("CAD_TEMP_DIR", str(launch_temp))
    for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR"):
        monkeypatch.setenv(name, str(launch_temp))
        monkeypatch.setenv(f"CAD_ORIG_{name}_SET", "0")
    monkeypatch.setenv("CAD_ORIG_TMPDIR_SET", "1")
    monkeypatch.setenv("CAD_ORIG_TMPDIR", "/site/eda/tmp")
    monkeypatch.setenv("XDG_CACHE_HOME", str(launch_temp / "cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(launch_temp / "runtime"))
    monkeypatch.setenv("CAD_ORIG_XDG_CACHE_HOME_SET", "0")
    monkeypatch.setenv("CAD_ORIG_XDG_RUNTIME_DIR_SET", "0")

    environment = eda_env()

    assert "CAD_TEMP_DIR" not in environment
    assert environment["TMPDIR"] == "/site/eda/tmp"
    assert all(name not in environment for name in ("TMP", "TEMP", "SQLITE_TMPDIR"))
    assert "XDG_CACHE_HOME" not in environment
    assert "XDG_RUNTIME_DIR" not in environment
