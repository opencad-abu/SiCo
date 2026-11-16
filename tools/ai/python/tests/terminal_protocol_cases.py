"""Terminal protocol regressions."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sicotemp import initialize
from terminal_fixtures import (
    PYQT_ROOT,
    protocol,
)


def test_parse_launch_options_matches_native_cli(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace path"
    workspace.mkdir()
    program = tmp_path / "agent"
    program.write_text("#!/bin/sh\n", encoding="ascii")
    program.chmod(0o755)

    options = protocol.parse_launch_options(
        [
            "--working-directory",
            str(workspace),
            "--",
            str(program),
            "argument with spaces",
            "--literal",
        ]
    )

    assert options.workspace == workspace.resolve()
    assert options.program == program.resolve()
    assert options.arguments == ("argument with spaces", "--literal")


@pytest.mark.parametrize(
    "arguments",
    (
        [],
        ["--"],
        ["--working-directory", "/missing", "--", "/bin/true"],
        ["--unknown", "value", "--", "/bin/true"],
        ["--", "/definitely/missing"],
    ),
)
def test_parse_launch_options_rejects_invalid_contract(arguments: list[str]) -> None:
    with pytest.raises(ValueError, match="working-directory"):
        protocol.parse_launch_options(arguments)


def test_initialize_cad_temp_uses_launch_cwd_and_overrides_stale_values(
    tmp_path: Path,
) -> None:
    launch = tmp_path / "launch"
    launch.mkdir()
    environment = {
        "TMPDIR": "/stale/tmp",
        "XDG_RUNTIME_DIR": "/stale/runtime",
    }

    temporary = initialize(environment, cwd=launch)

    assert temporary == launch / ".sico" / "ai"
    assert environment["SICO_TEMP_DIR"] == str(temporary.parent)
    assert all(
        environment[name] == str(temporary)
        for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR")
    )
    assert environment["XDG_CACHE_HOME"] == str(temporary / "cache")
    assert environment["XDG_RUNTIME_DIR"] == str(temporary / "runtime")
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert (temporary / "runtime").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("configured", ("relative/.cad", "/tmp/not-cad"))
def test_initialize_cad_temp_rejects_invalid_configured_path(
    tmp_path: Path, configured: str
) -> None:
    environment = {"SICO_TEMP_DIR": configured}
    with pytest.raises(ValueError, match="SICO_TEMP_DIR must end in .sico"):
        initialize(environment, cwd=tmp_path)


def test_control_decoder_handles_split_lines_unknown_commands_and_shutdown() -> None:
    decoder = protocol.ControlDecoder()

    assert decoder.feed(b"sh").commands == ()
    first = decoder.feed(b"ow\nunknown\n")
    assert first.commands == ("show",)
    assert not first.shutdown
    final = decoder.feed(b"shutdown\nshow\n")
    assert final.commands == ("shutdown",)
    assert final.shutdown
    assert decoder.feed(b"show\n").shutdown


def test_control_decoder_treats_eof_and_oversized_command_as_shutdown() -> None:
    decoder = protocol.ControlDecoder(maximum_bytes=8)
    oversized = decoder.feed(b"123456789")

    assert oversized.shutdown and oversized.oversized
    assert protocol.ControlDecoder().eof().shutdown


def test_parse_control_fd_adopts_only_readable_pipe() -> None:
    read_fd, write_fd = os.pipe()
    environment = {protocol.CONTROL_FD_ENV: str(read_fd)}
    adopted = None
    try:
        adopted = protocol.parse_control_fd(environment)
        assert adopted == read_fd
        assert protocol.CONTROL_FD_ENV not in environment
        assert os.get_blocking(adopted) is False
    finally:
        os.close(write_fd)
        if adopted is not None:
            os.close(adopted)

    environment = {protocol.CONTROL_FD_ENV: "1"}
    assert protocol.parse_control_fd(environment) is None
    assert protocol.CONTROL_FD_ENV not in environment


@pytest.mark.parametrize(
    ("choice", "running", "bypass", "expected"),
    (
        ("cancel", True, False, (False, False)),
        ("minimize", True, False, (False, True)),
        ("exit", True, False, (True, False)),
        ("cancel", False, False, (True, False)),
        ("cancel", True, True, (True, False)),
    ),
)
def test_close_decision_matches_native_policy(
    choice: str, running: bool, bypass: bool, expected: tuple[bool, bool]
) -> None:
    assert (
        protocol.close_decision(choice, session_running=running, bypass_confirmation=bypass)
        == expected
    )


def test_package_import_does_not_load_pyqt5(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(PYQT_ROOT))
    before = set(sys.modules)
    package = __import__("cad_ai_terminal")

    assert package.CONTROL_FD_ENV == "SICO_AI_CONTROL_FD"
    assert not any(name.startswith("PyQt5") for name in set(sys.modules) - before)
