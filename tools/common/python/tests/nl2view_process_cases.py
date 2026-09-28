"""nl2view process behavior cases."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cadview.cdslib import resolve_library_path
from nl2view_fixtures import (
    ENTRY,
    _process_exists,
    _run,
    _wait_for_file,
    _write_signal_recording_importer,
    _write_slow_importer,
)

def test_term_stops_importer_and_its_descendants(tmp_path: Path) -> None:
    source = tmp_path / "top.dspf"
    source.write_text("*|DSPF 1.0\n", encoding="utf-8")
    library = tmp_path / "work"
    library.mkdir()
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    importer = _write_slow_importer(tmp_path / "slow-cdsTextTo5x")
    parent_pid_file = tmp_path / "parent.pid"
    child_pid_file = tmp_path / "child.pid"
    environment = os.environ.copy()
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "NL2VIEW_TEST_PARENT_PID": str(parent_pid_file),
            "NL2VIEW_TEST_CHILD_PID": str(child_pid_file),
        }
    )
    process = subprocess.Popen(
        [
            str(ENTRY),
            "--cds-text-to-5x",
            str(importer),
            "--format",
            "dspf",
            "--library",
            "work",
            "--cell",
            "top",
            str(source),
        ],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_file(parent_pid_file)
        _wait_for_file(child_pid_file)
        parent_pid = int(parent_pid_file.read_text())
        child_pid = int(child_pid_file.read_text())

        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)

        assert process.returncode == 128 + signal.SIGTERM, (stdout, stderr)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and (
            _process_exists(parent_pid) or _process_exists(child_pid)
        ):
            time.sleep(0.05)
        assert not _process_exists(parent_pid)
        assert not _process_exists(child_pid)
        assert not (library / "top/dspfText").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

def test_term_is_forwarded_to_importer_process_group(tmp_path: Path) -> None:
    source = tmp_path / "top.dspf"
    source.write_text("*|DSPF 1.0\n", encoding="utf-8")
    (tmp_path / "work").mkdir()
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    importer = _write_signal_recording_importer(tmp_path / "signal-cdsTextTo5x")
    ready = tmp_path / "ready.pid"
    received = tmp_path / "signal.txt"
    environment = os.environ.copy()
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "NL2VIEW_TEST_READY": str(ready),
            "NL2VIEW_TEST_SIGNAL": str(received),
        }
    )
    process = subprocess.Popen(
        [
            str(ENTRY),
            "--cds-text-to-5x",
            str(importer),
            "--format",
            "dspf",
            "--library",
            "work",
            "--cell",
            "top",
            str(source),
        ],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_file(ready)
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)

        assert process.returncode == 128 + signal.SIGTERM
        _wait_for_file(received)
        assert received.read_text() == str(int(signal.SIGTERM))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

def test_restores_eda_library_path_for_cadence_child(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "work",
        "--cell",
        "top",
        str(source),
        extra_environment={
            "LD_LIBRARY_PATH": "/production/python/lib",
            "NL2VIEW_ORIG_LD_LIBRARY_PATH": "/eda/virtuoso/lib",
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    assert payload["ld_library_path"] == "/eda/virtuoso/lib"

def test_restores_mts_netlistor_eda_library_path_for_cadence_child(
    tmp_path: Path,
) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "work",
        "--cell",
        "top",
        str(source),
        extra_environment={
            "LD_LIBRARY_PATH": "/python/worker/lib",
            "MTS_NETLISTOR_ORIG_LD_LIBRARY_PATH": "/eda/virtuoso/lib",
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    assert payload["ld_library_path"] == "/eda/virtuoso/lib"

@pytest.mark.parametrize(
    "foreign_marker",
    (
        "RCE_ORIG_LD_LIBRARY_PATH",
        "DRC_ORIG_LD_LIBRARY_PATH",
        "LVS_ORIG_LD_LIBRARY_PATH",
        "LEF_ORIG_LD_LIBRARY_PATH",
    ),
)
def test_does_not_restore_other_flow_library_paths(
    tmp_path: Path, foreign_marker: str
) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "work",
        "--cell",
        "top",
        str(source),
        extra_environment={foreign_marker: "/foreign/eda/lib"},
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    assert payload["ld_library_path"] == ""

def test_cadence_child_does_not_inherit_python_or_linker_injection(
    tmp_path: Path,
) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "work",
        "--cell",
        "top",
        str(source),
        extra_environment={
            "PYTHONHOME": "/bad/python/home",
            "PYTHONPATH": "/bad/python/path",
            "LD_PRELOAD": "/bad/preload.so",
            "LD_AUDIT": "/bad/audit.so",
            "CDS_MPS_SESSION": "virtuoso405942",
            "CDS_MPS_HOST": "work-srv",
            "CDS_MPS_FUTURE_SELECTOR": "must-not-leak",
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    assert payload["mps_selectors"] == []
    assert payload["pythonhome"] is None
    assert payload["pythonpath"] is None
    assert payload["ld_preload"] is None
    assert payload["ld_audit"] is None

def test_direct_cds_lib_debug_resolution_detaches_mps_environment(tmp_path: Path) -> None:
    cds = tmp_path / "cds.lib"
    library = tmp_path / "work"
    library.mkdir()
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    record = tmp_path / "cdslib-debug-env.json"
    debug = tmp_path / "cdsLibDebug"
    debug.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        f"open({str(record)!r}, 'w', encoding='utf-8').write(json.dumps(sorted(name for name in os.environ if name.startswith('CDS_MPS_'))))\n"
        "print('Libraries defined:')\n"
        f"print('  1 work {str(library)}')\n",
        encoding="utf-8",
    )
    debug.chmod(0o755)

    resolved = resolve_library_path(
        cds,
        "work",
        environ={
            "PATH": os.environ.get("PATH", ""),
            "CDS_MPS_SESSION": "virtuoso405942",
            "CDS_MPS_FUTURE_SELECTOR": "must-not-leak",
        },
        cadence_executable=str(debug),
    )

    assert resolved == library.resolve()
    assert json.loads(record.read_text(encoding="utf-8")) == []
