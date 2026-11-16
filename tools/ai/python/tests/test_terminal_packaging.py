from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
from pathlib import Path

_SCRIPT_ROOT = Path(__file__).resolve().parents[2] / "terminal" / "scripts"
_RELEASE_VERIFIER = _SCRIPT_ROOT / "verify_release_abi.sh"
_CODEX_LOCK = _SCRIPT_ROOT.parent / "codex-runtime.lock"


def _fallback_json(label: str = "reference") -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "metadata": {
                "function_count": 0,
                "document_count": 0,
                "copyrights": [label],
            },
            "functions": [],
        },
        separators=(",", ":"),
    ).encode()


def _create_database(path: Path, label: str, marker: int = 0) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA user_version = 1;
            CREATE TABLE functions (
                reference_id TEXT PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE,
                usage TEXT NOT NULL,
                description TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                returns_text TEXT NOT NULL,
                example TEXT NOT NULL,
                doc_set TEXT NOT NULL,
                document TEXT NOT NULL,
                doc_title TEXT NOT NULL,
                product_version TEXT NOT NULL,
                body TEXT NOT NULL
            );
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        connection.execute(f"PRAGMA application_id = {marker}")
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("function_count", "0"),
                ("document_count", "0"),
                ("copyrights", json.dumps([label])),
            ],
        )


def _create_common_payload(
    stage: Path,
    *,
    label: str = "reference",
    database_marker: int = 0,
    fallback: bytes | None = None,
    fallback_mtime: int = 0,
) -> None:
    (stage / "README.md").write_text("release\n", encoding="utf-8")
    for relative in (
        "LICENSES",
        "docs",
        "python",
        "reference",
        "skill",
        "skill++",
        "skills",
    ):
        (stage / relative).mkdir(parents=True)
    reference = stage / "reference" / "cadence-skill"
    reference.mkdir()
    _create_database(reference / "skill_api.sqlite3", label, database_marker)
    encoded_fallback = _fallback_json(label) if fallback is None else fallback
    (reference / "skill_api.json.gz").write_bytes(
        gzip.compress(encoded_fallback, mtime=fallback_mtime)
    )
    (reference / "README.md").write_text("reference\n", encoding="utf-8")
    (reference / "NOTICE.md").write_text("notice\n", encoding="utf-8")
    (reference / "build_reference.py").write_text("# generator\n", encoding="utf-8")


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _create_fake_release_tools(tmp_path: Path) -> tuple[Path, Path]:
    tool_bin = tmp_path / "tool-bin"
    readelf = tool_bin / "readelf"
    _write_executable(readelf)
    readelf.write_text(
        "#!/bin/sh\n"
        "mode=$1\n"
        "shift\n"
        "case $1 in */runtime/ripgrep/*/bin/rg) exec /usr/bin/readelf \"$mode\" \"$@\" ;; esac\n"
        "case $mode in\n"
        "  -h)\n"
        "    grep -aFq FAKEELF \"$1\" || exit 1\n"
        "    printf '%s\\n' '  Class:                             ELF64'\n"
        "    printf '%s\\n' '  Machine:                           Advanced Micro Devices X86-64'\n"
        "    printf '%s\\n' '  Type:                              DYN (Shared object file)'\n"
        "    ;;\n"
        "  -l)\n"
        "    grep -aFq FAKEELF \"$1\" || exit 1\n"
        "    ;;\n"
        "  -d)\n"
        "    case $1 in\n"
        "      */QTermWidget.abi3.so)\n"
        "        printf '%s\\n' '  Shared library: [libqtermwidget5.so.1]'\n"
        "        printf '%s\\n' '  Library rpath: [$ORIGIN/../lib]'\n"
        "        ;;\n"
        "    esac\n"
        "    ;;\n"
        "  --version-info) ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="ascii",
    )
    readelf.chmod(0o755)

    python = tmp_path / "production-python"
    python.write_text(
        "#!/bin/sh\n"
        "runtime_python=${PYTHONPATH%%:*}\n"
        "printf '%s\\n' 5.15.11 5.15.14 5.15.14 \\\n"
        "  /opt/cad-python/site-packages/PyQt5/__init__.py \\\n"
        "  \"$runtime_python/QTermWidget.abi3.so\"\n",
        encoding="ascii",
    )
    python.chmod(0o755)
    return tool_bin, python


def _create_valid_terminal_stage(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    stage = tmp_path / "build" / "ai"
    stage.mkdir(parents=True)
    _create_common_payload(stage)
    _create_terminal_stage_runtime(stage)
    (stage / "python" / "QTermWidget.abi3.so").write_bytes(
        b"FAKEELF binding\nCAD_AI_QTERMWIDGET_SIP_ABI=12.11\0"
    )
    (stage / "lib").mkdir()
    (stage / "lib" / "libqtermwidget5.so.1").write_bytes(b"FAKEELF library\n")
    tool_bin, python = _create_fake_release_tools(tmp_path)

    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{tool_bin}{os.pathsep}{environment['PATH']}",
            "SICO_PYTHON": str(python),
            "SICO_AI_PYTHON_LIBRARY_PATH": "/opt/cad-python/lib",
        }
    )
    return stage, environment


def _create_terminal_stage_runtime(stage: Path) -> None:
    _write_executable(stage / "bin" / "aiassistant")
    _write_executable(stage / "bin" / "sico-ai-pinyin")
    _write_executable(stage / "bin" / "sico-ai-terminal")
    _write_executable(stage / "bin" / "sico-ai-terminal-pyqt")
    (stage / "python" / "sico-ai").write_text("# launcher\n", encoding="ascii")
    (stage / "python" / "cadai").mkdir()
    (stage / "python" / "cad_ai_terminal").mkdir()
    (stage / "share" / "qtermwidget5" / "kb-layouts").mkdir(parents=True)
    (stage / "share" / "qtermwidget5" / "color-schemes").mkdir()
    (stage / "share" / "licenses" / "qtermwidget5").mkdir(parents=True)
    (stage / "share/sico/icons/brand").mkdir(parents=True)
    (stage / "share/sico/icons/brand/logo.png").write_bytes(b"logo")
    (stage / "share" / "qtermwidget5" / "kb-layouts" / "default.keytab").write_text(
        "keymap\n", encoding="ascii"
    )
    (
        stage
        / "share"
        / "qtermwidget5"
        / "color-schemes"
        / "BreezeModified.colorscheme"
    ).write_text("scheme\n", encoding="ascii")
    for name in ("LICENSE", "LICENSE.BSD-3-clause", "LICENSE.LGPL2+"):
        (stage / "share" / "licenses" / "qtermwidget5" / name).write_text(
            "license\n", encoding="ascii"
        )
    _create_fake_codex(stage)
    shutil.copytree(_SCRIPT_ROOT.parents[1] / "runtime/ripgrep", stage / "runtime/ripgrep")
    for notice in (_SCRIPT_ROOT.parents[1] / "LICENSES").glob("ripgrep-*"):
        shutil.copy2(notice, stage / "LICENSES" / notice.name)


def _create_fake_codex(stage: Path) -> None:
    source = _CODEX_LOCK.read_text(encoding="ascii")
    values = dict(line.split("=", 1) for line in source.splitlines())
    native = stage / "runtime/codex/x86_64-unknown-linux-musl/bin"
    executable = native / "codex"
    _write_executable(
        executable,
    )
    executable.write_text(
        "#!/bin/sh\n"
        "if [ \"${1-}\" = --version ]; then\n"
        f"  printf '%s\\n' '{values['cli_version']}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
        "FAKEELF\n",
        encoding="ascii",
    )
    executable.chmod(0o755)
    companion = native / "codex-code-mode-host"
    companion.write_bytes(b"FAKEELF code-mode companion\n")
    companion.chmod(0o755)
    # The shipped release carries the stripped identity recorded as release_*.
    values["release_binary_size"] = str(executable.stat().st_size)
    values["release_binary_sha256"] = hashlib.sha256(
        executable.read_bytes()
    ).hexdigest()
    values["release_code_host_binary_size"] = str(companion.stat().st_size)
    values["release_code_host_binary_sha256"] = hashlib.sha256(
        companion.read_bytes()
    ).hexdigest()
    manifest = stage / "runtime/codex/x86_64-unknown-linux-musl/MANIFEST.txt"
    manifest.write_text(
        "".join(f"{key}={values[key]}\n" for key in values), encoding="ascii"
    )


def test_release_verifier_requires_the_stripped_codex_identity(tmp_path: Path) -> None:
    stage, environment = _create_valid_terminal_stage(tmp_path)
    manifest = stage / "runtime/codex/x86_64-unknown-linux-musl/MANIFEST.txt"
    values = dict(line.split("=", 1) for line in manifest.read_text().splitlines())
    # An unstripped stage carries the pinned upstream binary instead.
    values["release_binary_sha256"] = values["binary_sha256"]
    manifest.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="ascii",
    )

    result = subprocess.run(
        [str(_RELEASE_VERIFIER), "rhel7", str(stage)],
        env=environment,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "bundled Codex executable checksum does not match its manifest" in result.stderr


def test_release_verifier_rejects_missing_code_mode_host(tmp_path: Path) -> None:
    stage, environment = _create_valid_terminal_stage(tmp_path)
    (
        stage / "runtime/codex/x86_64-unknown-linux-musl/bin/codex-code-mode-host"
    ).unlink()

    result = subprocess.run(
        [str(_RELEASE_VERIFIER), "rhel7", str(stage)],
        env=environment,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "bundled Codex code-mode host is unavailable" in result.stderr


def test_release_verifier_rejects_directory_as_aiassistant(tmp_path: Path) -> None:
    stage, environment = _create_valid_terminal_stage(tmp_path)
    assistant = stage / "bin" / "aiassistant"
    assistant.unlink()
    assistant.mkdir()

    result = subprocess.run(
        [str(_RELEASE_VERIFIER), "rhel7", str(stage)],
        env=environment,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "AI Assistant launcher is unavailable" in result.stderr


def test_release_verifier_rejects_nested_forbidden_artifacts(tmp_path: Path) -> None:
    stage, environment = _create_valid_terminal_stage(tmp_path)
    forbidden = stage / "vendor" / "qt" / "libQt5Core.so.5"
    forbidden.parent.mkdir(parents=True)
    forbidden.write_bytes(b"not even an ELF")

    result = subprocess.run(
        [str(_RELEASE_VERIFIER), "rhel7", str(stage)],
        env=environment,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "production-owned Python/Qt artifact" in result.stderr
    assert str(forbidden) in result.stderr


def test_release_verifier_rejects_bundled_python_or_qt_archives(
    tmp_path: Path,
) -> None:
    for filename in ("PyQt5-runtime.whl", "qt-runtime.zip"):
        case_root = tmp_path / filename
        case_root.mkdir()
        stage, environment = _create_valid_terminal_stage(case_root)
        forbidden = stage / "vendor" / filename
        forbidden.parent.mkdir(parents=True)
        forbidden.write_bytes(b"opaque runtime archive")

        result = subprocess.run(
            [str(_RELEASE_VERIFIER), "rhel7", str(stage)],
            env=environment,
            check=False,
            text=True,
            capture_output=True,
        )

        assert result.returncode != 0
        assert "production-owned Python/Qt artifact" in result.stderr
        assert str(forbidden) in result.stderr


def test_release_verifier_rejects_unexpected_elf(tmp_path: Path) -> None:
    stage, environment = _create_valid_terminal_stage(tmp_path)
    unexpected = stage / "diagnostics" / "unexpected-terminal"
    unexpected.parent.mkdir(parents=True)
    shutil.copy2(stage / "python" / "QTermWidget.abi3.so", unexpected)

    result = subprocess.run(
        [str(_RELEASE_VERIFIER), "rhel7", str(stage)],
        env=environment,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "unexpected ELF binary" in result.stderr
    assert str(unexpected) in result.stderr


def test_release_verifier_rejects_obsolete_shutdown_marker(tmp_path: Path) -> None:
    stage, environment = _create_valid_terminal_stage(tmp_path)
    library = stage / "lib" / "libqtermwidget5.so.1"
    marker = b"the terminal process is still running, trying to stop it by SIGHUP"
    with library.open("ab") as handle:
        handle.write(marker + b"\0" + b"padding-after-marker\0" * 65536)

    result = subprocess.run(
        [str(_RELEASE_VERIFIER), "rhel7", str(stage)],
        env=environment,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "lacks the session-shutdown patch" in result.stderr


def test_release_verifier_rejects_wrong_qtermwidget_sip_abi(tmp_path: Path) -> None:
    stage, environment = _create_valid_terminal_stage(tmp_path)
    binding = stage / "python" / "QTermWidget.abi3.so"
    binding.write_bytes(binding.read_bytes().replace(b"12.11", b"12.15"))

    result = subprocess.run(
        [str(_RELEASE_VERIFIER), "rhel7", str(stage)],
        env=environment,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "does not declare required SIP ABI 12.11" in result.stderr
