"""Installation-owned Python selection and native-only AIVW boundaries."""

from types import SimpleNamespace

import pytest

from aivworkbench import runtime_info
from aivworkbench.executors import virtuoso_support


def test_runtime_report_derives_one_interpreter_from_sico_home(tmp_path, monkeypatch):
    root = tmp_path / "sico"
    monkeypatch.setenv("SICO_HOME", str(root))
    monkeypatch.setenv("SICO_PYTHON", "/stale/python3")
    monkeypatch.setenv("SICO_PYTHON_ROOT", "/stale/python")

    report = runtime_info.collect_runtime_info()

    assert report["CAD_PYTHON_ROOT"] == str(root / "runtime/python")
    assert report["CAD_PYTHON"] == str(root / "runtime/python/bin/python3")


def test_missing_installation_runtime_fails_without_using_selectors(
        tmp_path, monkeypatch):
    root = tmp_path / "sico"
    monkeypatch.setenv("SICO_HOME", str(root))
    monkeypatch.setenv("SICO_PYTHON", "/stale/python3")
    monkeypatch.setenv("SICO_PYTHON_ROOT", "/stale/python")

    with pytest.raises(RuntimeError, match=r"\$SICO_HOME/runtime/python is not a directory"):
        runtime_info.production_python()


def test_production_python_returns_the_installation_runtime(tmp_path, monkeypatch):
    python = tmp_path / "sico/runtime/python/bin/python3"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    python.chmod(0o755)
    monkeypatch.setattr(
        runtime_info,
        "validate_production_runtime",
        lambda: {"CAD_PYTHON": str(python)},
    )
    assert runtime_info.production_python() == str(python)


def test_native_normalizer_reports_unavailable_without_source_or_process(tmp_path, monkeypatch):
    monkeypatch.setattr(virtuoso_support, "__file__", "/runtime/virtuoso_support.abi3.so")
    monkeypatch.setattr(virtuoso_support.subprocess, "run",
                        lambda *a, **k: pytest.fail("source execution"))
    log = tmp_path / "normalize.log"
    result = virtuoso_support._run_normalizer(
        tmp_path / "input", tmp_path / "symbol", tmp_path / "out", tmp_path / "manifest",
        log, SimpleNamespace(environment={"SICO_AI_TOKEN": "never-export"}))
    assert result["status"] == "BLOCKED_ENVIRONMENT"
    assert result["code"] == "normalizer_unavailable" and result["command"] == []
    assert "never-export" not in log.read_text()
    assert not (tmp_path / "out").exists()
