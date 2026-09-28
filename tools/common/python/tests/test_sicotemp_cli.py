"""The state subprocess emits data only, with safe shell environment values."""

import json
import shlex
import subprocess
import pytest
import sicotemp_cli
from sicotemp_cli import main, response


def test_state_prefix_quotes_arbitrary_environment_data(tmp_path):
    marker = tmp_path / "must not exist"
    original = f"space ' $(touch {shlex.quote(str(marker))}) `id`"
    raw = response(tmp_path, environment={"TMPDIR": original})
    assert raw.startswith('(\"sico.state.environment.v1\" ')
    decoder = json.JSONDecoder()
    values = []
    remaining = raw[1:-1]
    while remaining.strip():
        value, end = decoder.raw_decode(remaining.lstrip())
        values.append(value)
        remaining = remaining.lstrip()[end:]
    result = subprocess.run(values[3] + " /usr/bin/env", shell=True, text=True,
                            capture_output=True, check=True, env={})
    assert "SICO_ORIG_TMPDIR=" + original in result.stdout.splitlines()
    assert not marker.exists()


@pytest.mark.parametrize("error_format", ["text", "skill"])
def test_state_cli_conflict_emits_no_success_data(tmp_path, monkeypatch, capsys, error_format):
    monkeypatch.setenv("SICO_TEMP_DIR", "relative/.sico")
    assert main(["--launch", str(tmp_path), "--error-format", error_format]) == 2
    output = capsys.readouterr()
    if error_format == "skill":
        assert output.out.startswith('(\"sico.state.error.v1\" ')
        assert "sico.state.environment.v1" not in output.out
        assert str(tmp_path / "relative") in output.out
        assert output.err == ""
    else:
        assert output.out == ""
        assert "SiCo state:" in output.err
    assert not (tmp_path / ".sico").exists()


@pytest.mark.parametrize("message", ['quote " \\ newline\ncontrol\x00', "x" * 10000])
def test_state_error_is_bounded_escaped_data(tmp_path, monkeypatch, capsys, message):
    def rejected(*args, **kwargs):
        raise ValueError(message)
    monkeypatch.setattr(sicotemp_cli, "response", rejected)
    assert main(["--launch", str(tmp_path), "--error-format", "skill"]) == 2
    output = capsys.readouterr()
    decoder = json.JSONDecoder()
    error, end = decoder.raw_decode(output.out[1:])
    detail, _ = decoder.raw_decode(output.out[1 + end:].lstrip())
    assert error == "sico.state.error.v1"
    assert len(detail) <= 2048
    assert "\x00" not in detail and "\n" not in detail
