"""The site wire is one Python 3.8-compatible stdlib-only file."""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
import sico_datahub_wire as wire
from sico_datahub import wire as package_wire
from sico_datahub.errors import HubError

ROOT = Path(__file__).resolve().parents[4]
WIRE = ROOT / "tools/common/python/sico_datahub_wire.py"


def test_wire_parses_as_python_38_and_uses_only_the_standard_library():
    source = WIRE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(WIRE), feature_version=(3, 8))
    imports = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    }
    imports.update(
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert imports <= {"json", "math", "re", "socket"}
    assert "sico" not in imports
    assert "from __future__" not in source


def test_wire_loads_in_an_isolated_interpreter_and_roundtrips():
    script = """
import importlib.util
import json
import sys
spec = importlib.util.spec_from_file_location("site_wire", sys.argv[1])
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)
row = wire.make_envelope(
    "sico.cad.objects.v1", "list", "o1",
    {"window_ref": "wctx_fixture_1", "query": ""},
    meta={"trace_id": "", "source": "python", "target": "python",
          "deadline_ms": 5000},
)
assert wire.decode_line(wire.encode_line(row)) == row
assert json.loads(wire.encode_envelope(row))["payload"]["query"] == ""
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(WIRE)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_package_wrapper_reuses_the_single_file_implementation():
    assert package_wire.HUB is wire.HUB
    assert package_wire.validate_envelope is wire.validate_envelope
    assert package_wire.encode_line is wire.encode_line


def test_wire_line_framing_is_single_message_and_strict_utf8():
    row = wire.make_envelope("sico.cad.windows.v1", "list", "w1", {
        "offset": 0,
        "limit": 200,
    })
    line = wire.encode_line(row)
    assert wire.decode_line(line) == row
    with pytest.raises(HubError, match="one newline"):
        wire.decode_line(line[:-1])
    with pytest.raises(HubError, match="more than one"):
        wire.decode_line(line + line)
    with pytest.raises(HubError, match="UTF-8"):
        wire.decode_envelope(b'{"contract":"\xff"}')


def test_wire_canonical_json_is_deterministic_and_finite():
    assert wire.canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'
    with pytest.raises(HubError, match="JSON"):
        wire.canonical_json({"value": object()})
    with pytest.raises(HubError, match="JSON"):
        wire.canonical_json({"value": float("nan")})


def test_wire_envelope_error_factory_uses_stable_codes():
    row = wire.error_envelope(
        "sico.cad.objects.v1",
        "list",
        "o1",
        "capability_not_available",
        "Object queries are unavailable",
    )
    assert row["status"] == "error"
    assert row["error"]["code"] == "capability_not_available"
    assert json.loads(wire.encode_envelope(row))["error"]["details"] == {}
    with pytest.raises(HubError):
        wire.error_envelope(
            "sico.cad.objects.v1", "list", "o1", "not_a_code", "bad"
        )


def test_wire_has_no_desktop_or_editor_imports():
    source = WIRE.read_text(encoding="utf-8")
    for forbidden in ("PyQt5", "import oa", "sico_ui", "editor", "workbench"):
        assert forbidden not in source
