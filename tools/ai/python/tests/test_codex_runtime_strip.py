"""Reduce the pinned Codex runtime to its recorded shipped identity."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

STRIPPER = (
    Path(__file__).resolve().parents[2] / "terminal/scripts/strip_codex_runtime.sh"
)
TARGET = "x86_64-unknown-linux-musl"
STRIP_FLAGS = (
    "--strip-all",
    "--remove-section=.gnu_debuglink",
    "--remove-section=.gnu_debugdata",
    "--remove-section=.debug_gdb_scripts",
)


def _identity(data: bytes) -> tuple[str, str]:
    return str(len(data)), hashlib.sha256(data).hexdigest()


def _stage(
    tmp_path: Path,
    codex: bytes,
    host: bytes,
    shipped: tuple[tuple[str, str], tuple[str, str]],
    upstream: tuple[tuple[str, str], tuple[str, str]] | None = None,
) -> Path:
    prefix = tmp_path / "stage/ai"
    native = prefix / f"runtime/codex/{TARGET}/bin"
    native.mkdir(parents=True)
    for name, payload in (("codex", codex), ("codex-code-mode-host", host)):
        binary = native / name
        binary.write_bytes(payload)
        binary.chmod(0o755)
    codex_size, codex_sha = _identity(codex)
    host_size, host_sha = _identity(host)
    if upstream is None:
        upstream = ((codex_size, codex_sha), (host_size, host_sha))
    values = {
        "format": "cad-ai.codex-runtime.v1",
        "target": TARGET,
        "binary_size": upstream[0][0],
        "binary_sha256": upstream[0][1],
        "code_host_binary_size": upstream[1][0],
        "code_host_binary_sha256": upstream[1][1],
        "release_binary_size": shipped[0][0],
        "release_binary_sha256": shipped[0][1],
        "release_code_host_binary_size": shipped[1][0],
        "release_code_host_binary_sha256": shipped[1][1],
    }
    (native.parent / "MANIFEST.txt").write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="ascii",
    )
    return prefix


def _run(prefix: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(STRIPPER), str(prefix)],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_strip_requires_a_pinned_identity(tmp_path: Path) -> None:
    prefix = _stage(
        tmp_path,
        b"unexpected codex\n",
        b"unexpected companion\n",
        shipped=(("2", "1" * 64), ("2", "1" * 64)),
        upstream=(("1", "0" * 64), ("1", "0" * 64)),
    )

    result = _run(prefix)

    assert result.returncode != 0
    assert "neither the pinned upstream binary nor the shipped one" in result.stderr
    assert (prefix / f"runtime/codex/{TARGET}/bin/codex").read_bytes() == (
        b"unexpected codex\n"
    )


def test_strip_keeps_an_already_shipped_runtime(tmp_path: Path) -> None:
    codex, host = b"# shipped codex\n", b"# shipped companion\n"
    prefix = _stage(tmp_path, codex, host, (_identity(codex), _identity(host)))

    result = _run(prefix)

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("already stripped:") == 2
    assert (prefix / f"runtime/codex/{TARGET}/bin/codex").read_bytes() == codex
    assert (prefix / f"runtime/codex/{TARGET}/bin/codex-code-mode-host").read_bytes() == host


@pytest.mark.skipif(shutil.which("strip") is None, reason="strip is unavailable")
def test_strip_reproduces_the_recorded_identity(tmp_path: Path) -> None:
    source = Path(shutil.which("true")).resolve()
    original = source.read_bytes()
    stripped_file = tmp_path / "stripped"
    subprocess.run(
        ["strip", *STRIP_FLAGS, "-o", str(stripped_file), str(source)],
        check=True,
    )
    stripped = stripped_file.read_bytes()
    assert stripped != original
    prefix = _stage(tmp_path, original, original, (_identity(stripped), _identity(stripped)))

    result = _run(prefix)

    assert result.returncode == 0, result.stderr
    native = prefix / f"runtime/codex/{TARGET}/bin"
    for name in ("codex", "codex-code-mode-host"):
        binary = native / name
        assert binary.read_bytes() == stripped
        assert os.access(binary, os.X_OK)
    assert not list((prefix / f"runtime/codex/{TARGET}").glob(".codex-strip.*"))
