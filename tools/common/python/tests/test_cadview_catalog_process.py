from cadview_catalog_fixtures import *

def test_dbaccess_catalog_private_protocol_ignores_stdout_noise_and_cleans_files(
    tmp_path: Path,
) -> None:
    cds, _ = _library_tree(tmp_path)
    marker = tmp_path / "protocol-path.txt"
    executable = _write_private_protocol_provider(tmp_path / "dbAccess", marker)

    catalog = dbaccess_catalog(cds, executable=str(executable))

    assert catalog.authoritative is True
    protocol_path = Path(marker.read_text(encoding="utf-8"))
    assert not protocol_path.exists()

def test_dbaccess_catalog_streams_output_before_process_exit(tmp_path: Path) -> None:
    cds, _ = _library_tree(tmp_path)
    marker = tmp_path / "protocol-path.txt"
    executable = tmp_path / "dbAccess"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
import time

protocol = os.environ["CADVIEW_CATALOG_OUTPUT"]
open(%r, "w", encoding="utf-8").write(protocol)
print("PDK initialization started", flush=True)
time.sleep(0.25)
with open(protocol, "w", encoding="utf-8") as stream:
    json.dump({"schema_version": 1, "authoritative": True, "libraries": []}, stream)
""" % str(marker),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    received: list[tuple[float, str]] = []
    started = time.monotonic()

    catalog = dbaccess_catalog(
        cds,
        executable=str(executable),
        output_callback=lambda text: received.append(
            (time.monotonic() - started, text)
        ),
    )

    assert catalog.authoritative is True
    assert "PDK initialization started" in "".join(text for _at, text in received)
    assert min(at for at, text in received if "PDK initialization" in text) < 0.20
    protocol_path = Path(marker.read_text(encoding="utf-8"))
    assert not protocol_path.exists()

def test_dbaccess_catalog_ignores_output_callback_failure(tmp_path: Path) -> None:
    cds, _ = _library_tree(tmp_path)
    marker = tmp_path / "protocol-path.txt"
    executable = _write_private_protocol_provider(tmp_path / "dbAccess", marker)

    def fail(_text: str) -> None:
        raise RuntimeError("log consumer closed")

    catalog = dbaccess_catalog(
        cds,
        executable=str(executable),
        output_callback=fail,
    )

    assert catalog.authoritative is True

def test_dbaccess_catalog_stream_decoder_preserves_split_utf8(tmp_path: Path) -> None:
    cds, _ = _library_tree(tmp_path)
    marker = tmp_path / "protocol-path.txt"
    executable = tmp_path / "dbAccess"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
import time

protocol = os.environ["CADVIEW_CATALOG_OUTPUT"]
open(%r, "w", encoding="utf-8").write(protocol)
sys.stdout.buffer.write(b"x" * (64 * 1024 - 1) + "初始化完成\\n".encode("utf-8"))
sys.stdout.flush()
time.sleep(0.05)
with open(protocol, "w", encoding="utf-8") as stream:
    json.dump({"schema_version": 1, "authoritative": True, "libraries": []}, stream)
""" % str(marker),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    received: list[str] = []

    dbaccess_catalog(
        cds,
        executable=str(executable),
        output_callback=received.append,
    )

    rendered = "".join(received)
    assert rendered.endswith("初始化完成\n")
    assert "\ufffd" not in rendered

def test_catalog_output_drain_uses_bounded_running_poll_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cadview.catalog_process as module

    cds, _ = _library_tree(tmp_path)
    marker = tmp_path / "protocol-path.txt"
    executable = _write_private_protocol_provider(
        tmp_path / "dbAccess", marker, delay=0.08
    )
    monkeypatch.setattr(module, "_CATALOG_STREAM_POLL_BYTES", 32)
    sizes: list[int] = []

    dbaccess_catalog(
        cds,
        executable=str(executable),
        output_callback=lambda text: sizes.append(len(text.encode("utf-8"))),
    )

    assert sizes
    assert max(sizes) <= 32

def test_dbaccess_catalog_private_protocol_cleans_files_on_provider_failure(
    tmp_path: Path,
) -> None:
    cds, _ = _library_tree(tmp_path)
    marker = tmp_path / "protocol-path.txt"
    executable = _write_private_protocol_provider(
        tmp_path / "dbAccess", marker, exit_status=7
    )

    with pytest.raises(CatalogError, match="exit status 7"):
        dbaccess_catalog(cds, executable=str(executable))

    protocol_path = Path(marker.read_text(encoding="utf-8"))
    assert not protocol_path.exists()

def test_dbaccess_catalog_falls_back_to_stdout_when_private_protocol_is_empty(
    tmp_path: Path,
) -> None:
    cds, _ = _library_tree(tmp_path)
    marker = tmp_path / "protocol-path.txt"
    executable = tmp_path / "dbAccess"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
protocol = os.environ["CADVIEW_CATALOG_OUTPUT"]
open(%r, "w", encoding="utf-8").write(protocol)
print(json.dumps({
    "schema_version": 1,
    "authoritative": True,
    "libraries": [{"name": "stdoutlib", "path": "/tmp/stdoutlib", "writable": True, "cells": []}],
}))
""" % str(marker),
        encoding="utf-8",
    )
    executable.chmod(0o755)

    catalog = dbaccess_catalog(cds, executable=str(executable))

    assert catalog.library("stdoutlib") is not None
    protocol_path = Path(marker.read_text(encoding="utf-8"))
    assert not protocol_path.exists()
