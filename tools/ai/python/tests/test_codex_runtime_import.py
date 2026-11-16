"""Atomic import of both pinned Codex runtime executables."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

IMPORTER = Path(__file__).resolve().parents[2] / "terminal/scripts/import_codex_runtime.sh"


def importer_fixture(tmp_path):
    terminal = tmp_path / "terminal"
    (terminal / "scripts").mkdir(parents=True)
    importer = terminal / "scripts/import_codex_runtime.sh"
    shutil.copy2(IMPORTER, importer)
    codex, host = tmp_path / "codex", tmp_path / "codex-code-mode-host"
    codex.write_text("#!/bin/sh\nprintf '%s\\n' 'codex-cli 0.162.1'\n", encoding="ascii")
    host.write_bytes(b"synthetic code-mode companion\n")
    values = {
        "format": "cad-ai.codex-runtime.v1",
        "version": "0.162.1",
        "cli_version": "codex-cli 0.162.1",
        "target": "x86_64-unknown-linux-musl",
    }
    for prefix, binary in (("", codex), ("code_host_", host)):
        values.update({
            prefix + "archive_url": "https://invalid.example/unused",
            prefix + "archive_member": binary.name + "-x86_64-unknown-linux-musl",
            prefix + "archive_size": "1",
            prefix + "archive_sha256": "0" * 64,
            prefix + "binary_size": str(binary.stat().st_size),
            prefix + "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        })
    (terminal / "codex-runtime.lock").write_text(
        "".join(key + "=" + value + "\n" for key, value in values.items()), encoding="ascii"
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    readelf = commands / "readelf"
    readelf.write_text(
        "#!/bin/sh\ncase $1 in\n-h) printf '%s\\n' 'Class: ELF64' "
        "'Machine: Advanced Micro Devices X86-64' 'Type: DYN' ;;\nesac\n",
        encoding="ascii",
    )
    readelf.chmod(0o755)
    environment = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"])
    return importer, codex, host, environment


@pytest.mark.parametrize("damaged", [False, True])
def test_import_requires_companion_and_publishes_both_atomically(tmp_path, damaged):
    importer, codex, host, environment = importer_fixture(tmp_path)
    if damaged:
        host.write_bytes(b"unexpected companion\n")
    output = tmp_path / "runtime"
    result = subprocess.run(
        [str(importer), str(output), str(codex), str(host)],
        env=environment, capture_output=True, text=True, timeout=15,
    )
    if damaged:
        assert result.returncode != 0
        assert "neither the pinned archive nor the pinned executable" in result.stderr
        assert not output.exists()
    else:
        assert result.returncode == 0, result.stderr
        native = output / "x86_64-unknown-linux-musl/bin"
        assert sorted(p.name for p in native.iterdir()) == ["codex", "codex-code-mode-host"]
        for source in (codex, host):
            assert (native / source.name).read_bytes() == source.read_bytes()
            assert os.access(native / source.name, os.X_OK)
        assert (native.parent / "MANIFEST.txt").read_bytes() == (
            importer.parent.parent / "codex-runtime.lock"
        ).read_bytes()
    assert not list(tmp_path.glob(".cad-codex-runtime.*"))
