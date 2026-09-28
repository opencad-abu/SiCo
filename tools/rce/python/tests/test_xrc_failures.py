from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.runner import RceRunner  # noqa: E402
from rcepy.stage_result import lvs_errors_ignored  # noqa: E402
from xrc_test_support import (  # noqa: E402
    expected_trace,
    install_fake_calibre,
    make_xrc_config,
)


def test_pdb_failure_stops_before_formatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    paths["output"] = paths["run_dir"] / "top.dspf"
    cfg = cfg.replace('netlist', value={"output_path": str(paths["output"])})
    paths["run_dir"].mkdir()
    paths["output"].write_text("stale netlist\n", encoding="utf-8")
    calibre = install_fake_calibre(tmp_path / "mgc")
    trace = tmp_path / "calibre.trace"
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    monkeypatch.setenv("FAKE_CALIBRE_TRACE", str(trace))
    monkeypatch.setenv("FAKE_PDB_FAIL", "1")

    with pytest.raises(RuntimeError, match=r"xrc_pdb.*23"):
        RceRunner(cfg).run()

    assert trace.read_text(encoding="utf-8").splitlines() == expected_trace(paths)[:2]
    assert not paths["output"].exists()
    assert list(paths["run_dir"].glob("top.dspf.*"))
    assert "xrc_pdb" in (paths["run_dir"] / "log" / "exit-abnormally").read_text()


def test_missing_mgc_home_calibre_writes_failure_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    monkeypatch.setenv("MGC_HOME", str(tmp_path / "missing-mgc"))

    with pytest.raises(RuntimeError, match="xrc_lvs could not start"):
        RceRunner(cfg).run()

    marker = paths["run_dir"] / "log" / "exit-abnormally"
    assert "could not start" in marker.read_text(encoding="utf-8")


def test_xrc_error_summary_stops_before_formatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    calibre = install_fake_calibre(tmp_path / "mgc")
    trace = tmp_path / "calibre.trace"
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    monkeypatch.setenv("FAKE_CALIBRE_TRACE", str(trace))
    monkeypatch.setenv("FAKE_XRC_ERRORS", "2")

    with pytest.raises(RuntimeError, match="reported errors"):
        RceRunner(cfg).run()

    assert trace.read_text(encoding="utf-8").splitlines() == expected_trace(paths)[:2]
    assert not paths["output"].exists()


def test_xrc_formatter_rejects_an_empty_netlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    calibre = install_fake_calibre(tmp_path / "mgc")
    trace = tmp_path / "calibre.trace"
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    monkeypatch.setenv("FAKE_CALIBRE_TRACE", str(trace))
    monkeypatch.setenv("FAKE_FMT_EMPTY", "1")

    with pytest.raises(RuntimeError, match=r"xrc_fmt.*non-empty output"):
        RceRunner(cfg).run()

    assert paths["output"].is_file()
    assert paths["output"].stat().st_size == 0


def test_ignored_lvs_mismatch_allows_a_nonzero_lvs_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    calibre = install_fake_calibre(tmp_path / "mgc")
    trace = tmp_path / "calibre.trace"
    cfg = cfg.replace('lvs', 'ignore_error', value=True)
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    monkeypatch.setenv("FAKE_CALIBRE_TRACE", str(trace))
    monkeypatch.setenv("FAKE_LVS_INCORRECT", "1")
    monkeypatch.setenv("FAKE_LVS_EXIT_CODE", "17")

    assert RceRunner(cfg).run() == 0

    assert trace.read_text(encoding="utf-8").splitlines() == expected_trace(paths)
    assert paths["output"].is_file()
    marker = paths["run_dir"] / "log" / "lvs-ignored-mismatch"
    assert "accepted by lvs.ignore_error=true" in marker.read_text(encoding="utf-8")


def test_ignored_lvs_mismatch_does_not_hide_formatter_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    calibre = install_fake_calibre(tmp_path / "mgc")
    cfg = cfg.replace('lvs', 'ignore_error', value=True)
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    monkeypatch.setenv("FAKE_CALIBRE_TRACE", str(tmp_path / "calibre.trace"))
    monkeypatch.setenv("FAKE_LVS_INCORRECT", "1")
    monkeypatch.setenv("FAKE_FMT_XRC_ERRORS", "2")

    with pytest.raises(RuntimeError, match="Calibre XRC reported errors"):
        RceRunner(cfg).run()

    assert paths["output"].is_file()
    assert not (paths["run_dir"] / "log" / "lvs-ignored-mismatch").exists()
    assert "Calibre XRC reported errors" in (
        paths["run_dir"] / "log" / "exit-abnormally"
    ).read_text(encoding="utf-8")


def test_ignored_lvs_setting_does_not_hide_non_mismatch_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = make_xrc_config(tmp_path)
    calibre = install_fake_calibre(tmp_path / "mgc")
    cfg = cfg.replace('lvs', 'ignore_error', value=True)
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    monkeypatch.setenv("FAKE_CALIBRE_TRACE", str(tmp_path / "calibre.trace"))
    monkeypatch.setenv("FAKE_LVS_FAILURE_CODE", "19")

    with pytest.raises(RuntimeError, match=r"xrc_lvs.*19"):
        RceRunner(cfg).run()

    assert not (tmp_path / "run/log/lvs-ignored-mismatch").exists()


def test_ignored_lvs_mismatch_without_formatter_output_has_no_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    calibre = install_fake_calibre(tmp_path / "mgc")
    cfg = cfg.replace('lvs', 'ignore_error', value=True)
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    monkeypatch.setenv("FAKE_CALIBRE_TRACE", str(tmp_path / "calibre.trace"))
    monkeypatch.setenv("FAKE_LVS_INCORRECT", "1")
    monkeypatch.setenv("FAKE_FMT_EMPTY", "1")

    with pytest.raises(RuntimeError, match="non-empty output"):
        RceRunner(cfg).run()

    assert not (paths["run_dir"] / "log/lvs-ignored-mismatch").exists()


@pytest.mark.parametrize("key", ["LVS_IGNORE_ERROR", "LVS_INGORE_ERROR"])
def test_legacy_lvs_ignore_keys_are_ignored(tmp_path: Path, key: str) -> None:
    cfg = RceConfig(
        raw={key: "TRUE"}, config_path=tmp_path / "legacy.toml"
    )

    assert not lvs_errors_ignored(cfg)


def test_canonical_lvs_ignore_flag_is_the_only_supported_key(tmp_path: Path) -> None:
    cfg = RceConfig(
        raw={
            "lvs": {"ignore_error": True},
            "LVS_IGNORE_ERROR": "TRUE",
            "LVS_INGORE_ERROR": "TRUE",
        },
        config_path=tmp_path / "rce.toml",
    )

    assert lvs_errors_ignored(cfg)
