"""Batch scheduling, environment, cancellation, and publication behavior."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from cadbatch.controller import BatchController, finalize_publications, load_manifest
from batch_controller_fixtures import _python_command, _write_manifest


def test_parallel_tasks_persist_json_and_tsv(tmp_path: Path) -> None:
    commands = [
        _python_command("import time; time.sleep(0.3); print('one')"),
        _python_command("import time; time.sleep(0.3); print('two')"),
    ]
    manifest_path = _write_manifest(tmp_path, commands, parallel=2)

    started = time.monotonic()
    result = BatchController(load_manifest(manifest_path)).run()
    elapsed = time.monotonic() - started

    assert result == 0
    assert elapsed < 0.58
    payload = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["temp_dir"] == str(tmp_path / "launch" / ".sico")
    assert payload["counts"]["succeeded"] == 2
    assert [task["status"] for task in payload["tasks"]] == [
        "succeeded",
        "succeeded",
    ]
    tsv = (tmp_path / "status.tsv").read_text(encoding="utf-8")
    assert tsv.startswith("index\tid\tlabel\tstatus\texit_code")
    assert tsv.count("\tsucceeded\t0\t") == 2
    assert "one" in (tmp_path / "run-1/task.log").read_text(encoding="utf-8")


def test_task_environment_uses_batch_launch_cad_temp(tmp_path: Path) -> None:
    command = _python_command(
        "import os, pathlib; "
        "pathlib.Path('temp-env.txt').write_text('\\n'.join("
        "os.environ[name] for name in "
        "('SICO_TEMP_DIR','TMPDIR','TMP','TEMP','SQLITE_TMPDIR',"
        "'XDG_CACHE_HOME','XDG_RUNTIME_DIR','PYTHONDONTWRITEBYTECODE')))"
    )
    manifest_path = _write_manifest(tmp_path, [command], parallel=1)

    assert BatchController(load_manifest(manifest_path)).run() == 0

    expected = str(tmp_path / "launch" / ".sico")
    assert (tmp_path / "run-1/temp-env.txt").read_text().splitlines() == [
        expected,
        expected,
        expected,
        expected,
        expected,
        str(Path(expected) / "cache"),
        str(Path(expected) / "runtime"),
        "1",
    ]
    assert (Path(expected) / "runtime").stat().st_mode & 0o777 == 0o700


def test_failed_task_does_not_stop_remaining_tasks(tmp_path: Path) -> None:
    marker = tmp_path / "continued"
    commands = [
        _python_command("raise SystemExit(7)"),
        _python_command(f"from pathlib import Path; Path({str(marker)!r}).touch()"),
    ]
    manifest_path = _write_manifest(tmp_path, commands, parallel=1)

    result = BatchController(load_manifest(manifest_path)).run()

    assert result == 1
    assert marker.is_file()
    payload = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert payload["status"] == "completed_with_errors"
    assert [task["status"] for task in payload["tasks"]] == [
        "failed",
        "succeeded",
    ]
    assert payload["tasks"][0]["exit_code"] == 7


def test_cli_cancellation_marks_active_and_pending_tasks(tmp_path: Path) -> None:
    commands = [
        _python_command("import time; time.sleep(30)"),
        _python_command("import time; time.sleep(30)"),
    ]
    manifest_path = _write_manifest(tmp_path, commands, parallel=1)
    entry = Path(__file__).resolve().parents[1] / "sico-batch"
    process = subprocess.Popen(
        [sys.executable, str(entry), "run", str(manifest_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status_path = tmp_path / "status.json"
        if status_path.is_file():
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            if payload["counts"]["running"] == 1:
                break
        time.sleep(0.05)
    else:
        process.kill()
        pytest.fail("batch task did not enter running state")

    process.terminate()
    stdout, _ = process.communicate(timeout=8)

    assert process.returncode == 130, stdout
    payload = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert payload["status"] == "canceled"
    assert payload["counts"]["canceled"] == 2
    assert all(task["status"] == "canceled" for task in payload["tasks"])


def test_manifest_rejects_duplicate_run_directories(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        [_python_command("pass"), _python_command("pass")],
    )
    text = manifest_path.read_text(encoding="utf-8")
    text = text.replace(str(tmp_path / "run-2"), str(tmp_path / "run-1"))
    manifest_path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate task run directory"):
        load_manifest(manifest_path)


def test_publication_finalize_updates_waiting_task(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, [_python_command("pass")], parallel=1)
    text = manifest_path.read_text(encoding="utf-8").replace(
        "publication_required = false", "publication_required = true"
    )
    manifest_path.write_text(text, encoding="utf-8")
    manifest = load_manifest(manifest_path)

    assert BatchController(manifest).run() == 0
    waiting = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert waiting["status"] == "awaiting_publication"
    assert waiting["tasks"][0]["status"] == "awaiting_publication"

    publications = tmp_path / "publications.tsv"
    publications.write_text(
        "id\tstatus\tmessage\n001\tsucceeded\tOA view created\n",
        encoding="utf-8",
    )
    assert finalize_publications(manifest, publications) == 0
    final = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert final["status"] == "completed"
    assert final["tasks"][0]["publication_message"] == "OA view created"


def test_publication_finalize_preserves_canceled_tasks(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        [_python_command("pass"), _python_command("pass")],
        parallel=1,
    )
    text = manifest_path.read_text(encoding="utf-8").replace(
        "publication_required = false", "publication_required = true"
    )
    manifest_path.write_text(text, encoding="utf-8")
    manifest = load_manifest(manifest_path)

    assert BatchController(manifest).run() == 0
    payload = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    payload["tasks"][1]["status"] = "canceled"
    payload["tasks"][1]["exit_code"] = 130
    (tmp_path / "status.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    publications = tmp_path / "publications.tsv"
    publications.write_text(
        "id\tstatus\tmessage\n001\tsucceeded\tOA view created\n",
        encoding="utf-8",
    )
    assert finalize_publications(manifest, publications) == 130
    final = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert final["status"] == "canceled"
    assert [task["status"] for task in final["tasks"]] == [
        "succeeded",
        "canceled",
    ]
