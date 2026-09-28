"""config artifact cases regressions."""

from __future__ import annotations
import json
from pathlib import Path
import pytest
from mtsnetlistor.artifacts import JobPaths, exclusive_job_lock
from mtsnetlistor.errors import RequestValidationError


def test_job_paths_use_proj_ade_db_dir_and_advisory_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJ_ADE_DB_DIR", str(tmp_path / "ade"))
    paths = JobPaths.create("work", "top", "schematic", "a" * 64)
    assert paths.namespace == (tmp_path / "ade" / "work.top.schematic")
    assert paths.raw.is_dir()
    assert paths.stable_output("top", ".spe").name == "top.spe"
    with exclusive_job_lock(paths.lock_file):
        lock_payload = json.loads(paths.lock_file.read_text(encoding="utf-8"))
        assert lock_payload["pid"] > 0
        assert lock_payload["ppid"] > 0
        assert "start_time" in lock_payload
        with pytest.raises(RequestValidationError, match="another MTS run"):
            with exclusive_job_lock(paths.lock_file):
                pass


def test_job_paths_reject_relative_or_missing_proj_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PROJ_ADE_DB_DIR", raising=False)
    with pytest.raises(RequestValidationError, match="PROJ_ADE_DB_DIR"):
        JobPaths.create("work", "top", "schematic", "a" * 64)
    monkeypatch.setenv("PROJ_ADE_DB_DIR", "relative")
    with pytest.raises(RequestValidationError, match="absolute"):
        JobPaths.create("work", "top", "schematic", "a" * 64)


def test_job_paths_allocate_unique_runs_when_timestamp_and_digest_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROJ_ADE_DB_DIR", str(tmp_path / "ade"))
    fixed = "b" * 64
    first = JobPaths.create("work", "top", "schematic", fixed)
    second = JobPaths.create("work", "top", "schematic", fixed)
    assert first.run != second.run
    assert first.run.exists() and second.run.exists()
    assert first.namespace == second.namespace


def test_job_paths_reject_digest_that_could_escape_run_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROJ_ADE_DB_DIR", str(tmp_path / "ade"))
    with pytest.raises(RequestValidationError, match="64-character hexadecimal"):
        JobPaths.create("work", "top", "schematic", "../escape")
