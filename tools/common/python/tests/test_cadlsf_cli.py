from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cadgui.protocol import TransferDocument, read_transfer
from cadlsf import HostInfo, QueueInfo
from cadlsf.cli import main
from cadlsf.model import ClusterSnapshot, Diagnostic


from cadlsf_fixtures import CAD_ROOT


def test_cli_writes_versioned_json_and_tsv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = ClusterSnapshot(
        status="ready",
        collected_at="2026-08-18T12:00:00Z",
        user="demo",
        selected_queue="normal",
        queues=(QueueInfo("normal", "Open:Active", True, 3, 1, 2),),
        hosts=(HostInfo("node01", "ok", True),),
        diagnostics=(Diagnostic("load_missing", "Load data unavailable"),),
    )

    class FakeCollector:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def snapshot(
            self,
            _queue: str | None = None,
            *,
            include_hosts: bool = True,
            include_jobs: bool = False,
        ) -> ClusterSnapshot:
            return snapshot

    monkeypatch.setattr("cadlsf.cli.LsfCollector", FakeCollector)
    output = tmp_path / "result.tsv"

    assert main(["snapshot", "--queue", "normal", "--format", "json"]) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["schema_version"] == 3
    assert rendered["hosts"][0]["name"] == "node01"

    assert (
        main(
            [
                "snapshot",
                "--queue",
                "normal",
                "--format",
                "tsv",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert read_transfer(output) == TransferDocument(
        records=(
            ("QUEUE", "normal"),
            ("SELECTED_QUEUE", "normal"),
            ("HOST", "node01"),
        ),
        status="ready",
        version="1",
    )


def test_executable_wrapper_has_help() -> None:
    completed = subprocess.run(
        [str(CAD_ROOT / "common/python/sico-lsf"), "--help"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert "{queues,hosts,snapshot,monitor}" in completed.stdout


def test_monitor_selector_refuses_existing_or_missing_output_parent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = tmp_path / "existing.tsv"
    existing.write_text("stale\n", encoding="ascii")
    assert main(["monitor", "--output", str(existing)]) == 2
    assert "already exists" in capsys.readouterr().err

    missing = tmp_path / "missing" / "result.tsv"
    assert main(["monitor", "--output", str(missing)]) == 2
    assert "directory does not exist" in capsys.readouterr().err


def test_monitor_output_enables_selector_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "selection.tsv"
    received: dict[str, object] = {}

    def fake_run_gui(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr("cadlsf.gui.app.run_gui", fake_run_gui)

    assert main(["monitor", "--queue", "normal", "--output", str(output)]) == 0
    assert received["initial_queue"] == "normal"
    assert received["output"] == output


def test_non_monitor_cli_does_not_import_qt() -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    code = (
        "import sys; from cadlsf.cli import main; "
        "print(any(name.startswith('PyQt5') for name in sys.modules)); "
        "rc=main(['--bqueues','missing-lsf-command','queues']); "
        "print(any(name.startswith('PyQt5') for name in sys.modules)); "
        "raise SystemExit(rc)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=CAD_ROOT / "common/python",
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
        env=environment,
    )

    lines = completed.stdout.splitlines()
    assert lines[0] == "False"
    assert lines[-1] == "False"
    assert completed.returncode == 2
