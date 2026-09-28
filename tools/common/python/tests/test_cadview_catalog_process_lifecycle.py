from cadview_catalog_fixtures import *

def test_dbaccess_catalog_drains_large_stdout_and_stderr(tmp_path: Path) -> None:
    """PDK diagnostics larger than an OS pipe must not cause a false timeout."""

    cds, _ = _library_tree(tmp_path)
    executable = tmp_path / "dbAccess"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys

# Each stream is deliberately larger than the usual pipe buffer.  Flush both
# before writing the protocol so a parent that only polls() would deadlock.
sys.stdout.write("stdout diagnostic " + "x" * (256 * 1024))
sys.stdout.flush()
sys.stderr.write("stderr diagnostic " + "y" * (256 * 1024))
sys.stderr.flush()
print(json.dumps({
    "schema_version": 1,
    "authoritative": True,
    "libraries": [{"name": "noisy", "path": "/tmp/noisy", "writable": True, "cells": []}],
}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    script = tmp_path / "catalog.il"
    script.write_text("fixture", encoding="utf-8")

    catalog = dbaccess_catalog(
        cds,
        executable=str(executable),
        script=script,
        timeout=2.0,
    )

    assert catalog.library("noisy") is not None

def test_dbaccess_catalog_detaches_from_inherited_mps_session(tmp_path: Path) -> None:
    cds, _ = _library_tree(tmp_path)
    record = tmp_path / "environment.json"
    executable = tmp_path / "dbAccess"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
open(%r, "w", encoding="utf-8").write(json.dumps(sorted(
    name for name in os.environ if name.startswith("CDS_MPS_")
)))
print(json.dumps({
    "schema_version": 1,
    "authoritative": True,
    "libraries": [],
}))
""" % str(record),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    script = tmp_path / "catalog.il"
    script.write_text("fixture", encoding="utf-8")

    catalog = dbaccess_catalog(
        cds,
        executable=str(executable),
        script=script,
        environ={
            "PATH": os.environ.get("PATH", ""),
            "CDS_MPS_SESSION": "virtuoso405942",
            "CDS_MPS_HOST": "work-srv",
            "CDS_MPS_FUTURE_SELECTOR": "must-not-leak",
        },
    )

    assert catalog.authoritative is True
    assert json.loads(record.read_text(encoding="utf-8")) == []

def test_dbaccess_catalog_rejects_bad_json(tmp_path: Path) -> None:
    cds, _ = _library_tree(tmp_path)
    executable = tmp_path / "dbAccess"
    executable.write_text("#!/bin/sh\nprintf 'not-json'\n", encoding="utf-8")
    executable.chmod(0o755)
    script = tmp_path / "catalog.il"
    script.write_text("fixture", encoding="utf-8")

    with pytest.raises(CatalogError, match="invalid JSON"):
        dbaccess_catalog(cds, executable=str(executable), script=script)

def test_dbaccess_catalog_supports_cancel_and_timeout(tmp_path: Path) -> None:
    cds, _ = _library_tree(tmp_path)
    executable = _write_provider(tmp_path / "dbAccess")
    script = tmp_path / "catalog.il"
    script.write_text("fixture", encoding="utf-8")

    canceled = Event()
    thread = Thread(
        target=lambda: (
            time.sleep(0.05),
            canceled.set(),
        ),
    )
    thread.start()
    with pytest.raises(CatalogCancelled):
        dbaccess_catalog(
            cds,
            executable=str(executable),
            script=script,
            cancel_event=canceled,
        )
    thread.join()

    with pytest.raises(CatalogTimeout):
        dbaccess_catalog(
            cds,
            executable=str(executable),
            script=script,
            timeout=0.01,
        )

def test_dbaccess_catalog_streams_final_output_when_cancelled(tmp_path: Path) -> None:
    cds, _ = _library_tree(tmp_path)
    marker = tmp_path / "ready"
    executable = tmp_path / "dbAccess"
    executable.write_text(
        """#!/usr/bin/env python3
import signal
import time

def stop(_signum, _frame):
    print("PDK shutdown complete", flush=True)
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
print("PDK initialization active", flush=True)
open(%r, "w", encoding="utf-8").write("ready")
while True:
    time.sleep(0.05)
""" % str(marker),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    script = tmp_path / "catalog.il"
    script.write_text("fixture", encoding="utf-8")
    canceled = Event()
    received: list[str] = []

    def cancel_when_ready() -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not marker.is_file():
            time.sleep(0.01)
        canceled.set()

    cancel_thread = Thread(target=cancel_when_ready)
    cancel_thread.start()
    with pytest.raises(CatalogCancelled):
        dbaccess_catalog(
            cds,
            executable=str(executable),
            script=script,
            cancel_event=canceled,
            output_callback=received.append,
        )
    cancel_thread.join(timeout=2)

    assert marker.is_file()
    rendered = "".join(received)
    assert "PDK initialization active" in rendered
    assert "PDK shutdown complete" in rendered

def test_dbaccess_catalog_timeout_is_responsive_during_continuous_output(
    tmp_path: Path,
) -> None:
    cds, _ = _library_tree(tmp_path)
    executable = tmp_path / "dbAccess"
    executable.write_text(
        """#!/usr/bin/env python3
import os
import time

chunk = b"continuous PDK initialization output " + b"x" * (128 * 1024)
while True:
    os.write(1, chunk)
    time.sleep(0.005)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    script = tmp_path / "catalog.il"
    script.write_text("fixture", encoding="utf-8")
    received = 0

    def count_output(text: str) -> None:
        nonlocal received
        received += len(text)

    started = time.monotonic()
    with pytest.raises(CatalogTimeout):
        dbaccess_catalog(
            cds,
            executable=str(executable),
            script=script,
            timeout=0.5,
            output_callback=count_output,
        )
    elapsed = time.monotonic() - started

    assert received > 0
    assert elapsed < 2.0

def test_dbaccess_catalog_stops_process_when_output_reader_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds, _ = _library_tree(tmp_path)
    stopped = tmp_path / "stopped"
    executable = tmp_path / "dbAccess"
    executable.write_text(
        """#!/usr/bin/env python3
import os
import signal
import time

stopped = %r

def stop(_signum, _frame):
    open(stopped, "w", encoding="utf-8").write("yes")
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
print("output before reader failure", flush=True)
while True:
    time.sleep(0.05)
""" % str(stopped),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    script = tmp_path / "catalog.il"
    script.write_text("fixture", encoding="utf-8")

    def fail_pread(*_args, **_kwargs):
        raise RuntimeError("reader failed")

    monkeypatch.setattr(os, "pread", fail_pread)
    with pytest.raises(RuntimeError, match="reader failed"):
        dbaccess_catalog(
            cds,
            executable=str(executable),
            script=script,
            timeout=2.0,
        )

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not stopped.is_file():
        time.sleep(0.01)
    assert stopped.is_file()
