from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from drcpy.runner import DrcRunner, open_rve
from drcpy.generator import generate_drc
from rcepy.config import RceConfig


def _config(
    tmp_path: Path, cpus: str = "1", run_mode: str | None = None
) -> RceConfig:
    drc = {"tool": "Calibre"}
    if run_mode is not None:
        drc["run_mode"] = run_mode
    return RceConfig(
        raw={
            "run": {"run_dir": str(tmp_path / "run")},
            "input": {
                "type": "OA",
                "schematic": {"cell": "inv_x1"},
                "layout": {"cell": "inv_x1"},
            },
            "drc": drc,
            "runtime": {"cpus": cpus},
        },
        config_path=tmp_path / "drc.toml",
    )


def _runset(tmp_path: Path) -> Path:
    path = tmp_path / "foundry.cal"
    path.write_text(
        'LAYOUT PRIMARY "old"\nLAYOUT PATH "old.gds"\n'
        "LAYOUT SYSTEM GDSII\nDRC RESULTS DATABASE \"old.out\" ASCII\n"
        'DRC SUMMARY REPORT "old.sum"\n',
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("cpus", ["1", "8"])
def test_calibre_drc_defaults_to_hierarchical_turbo_mode(
    tmp_path: Path, cpus: str
) -> None:
    runner = DrcRunner(_config(tmp_path, cpus))

    drc_stage = next(stage for stage in runner._stages() if stage.name == "drc")

    assert drc_stage.command == [
        "calibre",
        "-drc",
        "-hier",
        "-turbo",
        cpus,
        str(tmp_path / "run" / "log" / "drc.cal"),
    ]


@pytest.mark.parametrize("cpus", ["1", "8"])
def test_flat_calibre_drc_omits_hierarchical_turbo_options(
    tmp_path: Path, cpus: str
) -> None:
    runner = DrcRunner(_config(tmp_path, cpus, "Flat"))

    drc_stage = next(stage for stage in runner._stages() if stage.name == "drc")

    assert drc_stage.command == [
        "calibre",
        "-drc",
        str(tmp_path / "run" / "log" / "drc.cal"),
    ]


def test_calibre_drc_rejects_invalid_run_mode(tmp_path: Path) -> None:
    runner = DrcRunner(_config(tmp_path, run_mode="parallel"))

    with pytest.raises(ValueError, match="drc.run_mode.*Hier.*Flat"):
        runner._stages()


def test_open_rve_launches_drc_results_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    results_db = tmp_path / "run" / "db" / "drc" / "cal_drc.out"
    results_db.parent.mkdir(parents=True)
    results_db.write_text("DRC ASCII results\n", encoding="utf-8")
    launched: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> object:
        launched.update(command=command, kwargs=kwargs)
        return object()

    monkeypatch.setattr("drcpy.runner.subprocess.Popen", fake_popen)
    for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR"):
        monkeypatch.delenv(name, raising=False)

    assert open_rve(cfg) == 0
    assert launched["command"] == ["calibre", "-rve", str(results_db)]
    kwargs = launched["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == str(tmp_path / "run")
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True
    assert all(
        name not in kwargs["env"]
        for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR")
    )


def test_open_rve_rejects_missing_drc_results_database(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="DRC results database"):
        open_rve(_config(tmp_path))


def test_eda_environment_restores_original_temp_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from drcpy.runner import eda_env

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


def test_eda_environment_restores_explicit_empty_original_library_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from drcpy.runner import eda_env

    monkeypatch.setenv("LD_LIBRARY_PATH", "/cad/python/lib")
    monkeypatch.setenv("DRC_ORIG_LD_LIBRARY_PATH", "")

    assert eda_env()["LD_LIBRARY_PATH"] == ""


def test_generate_drc_writes_inputs_and_includes_rule_file(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    foundry_deck = _runset(tmp_path)
    cfg = cfg.replace('drc', 'runset_file', value=foundry_deck.name)

    output = generate_drc(cfg, cfg.context())

    assert output.read_text(encoding="utf-8") == (
        'LAYOUT PRIMARY "inv_x1"\n'
        f'LAYOUT PATH "{tmp_path / "run" / "db" / "gds" / "inv_x1.gds"}"\n'
        "LAYOUT SYSTEM GDSII\n"
        f'DRC RESULTS DATABASE "{tmp_path / "run" / "db" / "drc" / "cal_drc.out"}" ASCII\n'
        f'DRC SUMMARY REPORT "{tmp_path / "run" / "log" / "cal_drc.sum"}"\n'
        "DRC ICSTATION YES\n"
        f'INCLUDE "{foundry_deck}"\n'
    )


def test_generate_drc_appends_enabled_custom_svrf_verbatim(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    foundry_deck = _runset(tmp_path)
    original_foundry_text = foundry_deck.read_text(encoding="utf-8")
    cfg = cfg.replace('drc', value={**cfg.section('drc'), **{
            "runset_file": str(foundry_deck),
            "custom_svrf_enable": True,
            "custom_svrf_command": 'DRC CHECK MAP\n// preserve "text"',
        }})
    output = generate_drc(cfg, cfg.context())
    text = output.read_text(encoding="utf-8")
    assert text.endswith('\n\nDRC CHECK MAP\n// preserve "text"\n')
    assert foundry_deck.read_text(encoding="utf-8") == original_foundry_text


def test_generate_drc_ignores_disabled_custom_svrf(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg = cfg.replace('drc', value={**cfg.section('drc'), **{
            "runset_file": str(_runset(tmp_path)),
            "custom_svrf_enable": False,
            "custom_svrf_command": "DRC MUST NOT BE WRITTEN",
        }})
    output = generate_drc(cfg, cfg.context())
    assert "DRC MUST NOT BE WRITTEN" not in output.read_text(encoding="utf-8")


def test_generate_drc_appends_rule_select_before_custom_svrf(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    foundry_deck = _runset(tmp_path)
    original_foundry_text = foundry_deck.read_text(encoding="utf-8")
    cfg = cfg.replace('drc', value={**cfg.section('drc'), **{
            "runset_file": str(foundry_deck),
            "rule_select_enable": True,
            "rule_select_groups": ["GAA", "GGT", "gaa"],
            "rule_select_checks": ["GT_1", "gAa", "AA_2"],
            "custom_svrf_enable": True,
            "custom_svrf_command": "DRC CHECK MAP",
        }})

    output = generate_drc(cfg, cfg.context())
    text = output.read_text(encoding="utf-8")

    assert text.endswith(
        '\n\nDRC SELECT CHECK "GAA" "GGT" "GT_1" "AA_2"\n\n'
        "DRC CHECK MAP\n"
    )
    assert foundry_deck.read_text(encoding="utf-8") == original_foundry_text


def test_generate_drc_leaves_rule_selection_disabled_by_default(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg = cfg.replace('drc', value={**cfg.section('drc'), **{
            "runset_file": str(_runset(tmp_path)),
            "rule_select_groups": ["GAA"],
        }})

    output = generate_drc(cfg, cfg.context())

    assert "DRC SELECT CHECK" not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("groups", ["GAA", [""], ["GAA\nDRC CHECK MAP"]])
def test_generate_drc_rejects_invalid_enabled_rule_selection(
    tmp_path: Path, groups: object
) -> None:
    cfg = _config(tmp_path)
    cfg = cfg.replace('drc', value={**cfg.section('drc'), **{
            "runset_file": str(_runset(tmp_path)),
            "rule_select_enable": True,
            "rule_select_groups": groups,
        }})

    with pytest.raises(ValueError, match="rule_select_groups|rule selection"):
        generate_drc(cfg, cfg.context())


def test_generate_drc_accepts_check_only_selection(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg = cfg.replace('drc', value={**cfg.section('drc'), **{
            "runset_file": str(_runset(tmp_path)),
            "rule_select_enable": True,
            "rule_select_groups": [],
            "rule_select_checks": ["M1_1", "M1_2"],
        }})

    output = generate_drc(cfg, cfg.context())

    assert 'DRC SELECT CHECK "M1_1" "M1_2"' in output.read_text(
        encoding="utf-8"
    )


def test_generate_drc_quotes_rule_names_with_svrf_symbols(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg = cfg.replace('drc', value={**cfg.section('drc'), **{
            "runset_file": str(_runset(tmp_path)),
            "rule_select_enable": True,
            "rule_select_groups": ["G-A", "G:ESD"],
            "rule_select_checks": ["M1+CHECK"],
        }})

    output = generate_drc(cfg, cfg.context())

    assert 'DRC SELECT CHECK "G-A" "G:ESD" "M1+CHECK"' in output.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "existing",
    [
        "DRC SELECT CHECK OLD_GROUP",
        "drc select check by layer M1",
        "tvf::DRC SELECT CHECK OLD_GROUP",
    ],
)
def test_generate_drc_rejects_existing_select_statement(
    tmp_path: Path, existing: str
) -> None:
    cfg = _config(tmp_path)
    foundry_deck = _runset(tmp_path)
    foundry_deck.write_text(
        foundry_deck.read_text(encoding="utf-8") + existing + "\n",
        encoding="utf-8",
    )
    cfg = cfg.replace('drc', value={**cfg.section('drc'), **{
            "runset_file": str(foundry_deck),
            "rule_select_enable": True,
            "rule_select_groups": ["GAA"],
        }})

    with pytest.raises(ValueError, match="existing DRC SELECT CHECK"):
        generate_drc(cfg, cfg.context())


def test_generate_drc_ignores_commented_existing_select(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    foundry_deck = _runset(tmp_path)
    foundry_deck.write_text(
        foundry_deck.read_text(encoding="utf-8")
        + "// DRC SELECT CHECK OLD_GROUP\n"
        + "/* DRC SELECT CHECK BY LAYER M1 */\n",
        encoding="utf-8",
    )
    cfg = cfg.replace('drc', value={**cfg.section('drc'), **{
            "runset_file": str(foundry_deck),
            "rule_select_enable": True,
            "rule_select_groups": ["GAA"],
        }})

    output = generate_drc(cfg, cfg.context())

    assert 'DRC SELECT CHECK "GAA"' in output.read_text(encoding="utf-8")


def test_generate_drc_rejects_select_statement_in_custom_svrf(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    cfg = cfg.replace('drc', value={**cfg.section('drc'), **{
            "runset_file": str(_runset(tmp_path)),
            "rule_select_enable": True,
            "rule_select_groups": ["GAA"],
            "custom_svrf_enable": True,
            "custom_svrf_command": "DRC SELECT CHECK EXTRA",
        }})

    with pytest.raises(ValueError, match="existing DRC SELECT CHECK"):
        generate_drc(cfg, cfg.context())


def test_generate_drc_keeps_wrapper_options_in_svrf_for_compile_time_tvf(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    foundry_deck = _runset(tmp_path)
    foundry_deck.write_text(
        "#! tvf\n"
        "tvf::VERBATIM {\n"
        + foundry_deck.read_text(encoding="utf-8")
        + "}\n",
        encoding="utf-8",
    )
    cfg = cfg.replace('drc', value={**cfg.section('drc'), **{
            "runset_file": str(foundry_deck),
            "rule_select_enable": True,
            "rule_select_groups": ["G-A"],
            "custom_svrf_enable": True,
            "custom_svrf_command": "DRC CHECK MAP",
        }})

    output = generate_drc(cfg, cfg.context())

    text = output.read_text(encoding="utf-8")
    assert f'INCLUDE "{foundry_deck}"' in text
    assert text.endswith('\n\nDRC SELECT CHECK "G-A"\n\nDRC CHECK MAP\n')


def test_generate_drc_rejects_empty_group_and_check_selection(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg = cfg.replace('drc', value={**cfg.section('drc'), **{
            "runset_file": str(_runset(tmp_path)),
            "rule_select_enable": True,
            "rule_select_groups": [],
            "rule_select_checks": [],
        }})

    with pytest.raises(ValueError, match="no rule group or check"):
        generate_drc(cfg, cfg.context())


def test_generate_drc_rejects_non_boolean_rule_select_enable(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    with pytest.raises(ValueError, match="rule_select_enable.*true or false"):
        cfg.replace("drc", "rule_select_enable", value="yes")
