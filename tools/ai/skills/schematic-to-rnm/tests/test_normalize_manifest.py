from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from normalize_manifest import BlockedInput, normalize


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "normalize_manifest.py"


def _symbol(*, order: list[str] | None = None) -> dict:
    terminals = [
        {"name": "VDD", "direction": "input", "data_type": "wreal", "port_kind": "supply"},
        {"name": "EN", "direction": "input", "data_type": "logic", "port_kind": "wire"},
        {"name": "VOUT", "direction": "output", "data_type": "wreal", "port_kind": "supply"},
    ]
    payload = {
        "ok": True,
        "kind": "symbol",
        "cellview": {"lib": "demo", "cell": "reg", "view": "symbol"},
        "terminals": terminals,
        "port_order": order or ["VDD", "EN", "VOUT"],
        "term_order": [],
        "truncated": False,
    }
    return payload


def _schematic() -> dict:
    return {
        "ok": True,
        "kind": "schematic",
        "cellview": {"lib": "demo", "cell": "reg", "view": "schematic"},
        "instances": [
            {
                "name": "u0",
                "lib": "demo",
                "cell": "leaf",
                "view": "symbol",
                "term_count": 3,
                "terminals": [
                    {"name": "VDD", "net": "VDD"},
                    {"name": "EN", "net": "EN"},
                    {"name": "VOUT", "net": "VOUT"},
                ],
            }
        ],
        "nets": [
            {"name": "VDD", "bits": 1, "signal_type": "supply", "global": True},
            {"name": "EN", "bits": 1, "signal_type": "digital", "global": False},
            {"name": "VOUT", "bits": 1, "signal_type": "supply", "global": False},
        ],
        "terminals": [
            {"name": "VDD", "direction": "input", "pins": [{"net": "VDD"}]},
            {"name": "EN", "direction": "input", "pins": [{"net": "EN"}]},
            {"name": "VOUT", "direction": "output", "pins": [{"net": "VOUT"}]},
        ],
        "truncated": False,
    }


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    schematic_path = tmp_path / "schematic.json"
    symbol_path = tmp_path / "symbol.json"
    schematic_path.write_text(json.dumps(_schematic()), encoding="utf-8")
    symbol_path.write_text(json.dumps(_symbol()), encoding="utf-8")
    return schematic_path, symbol_path


def test_normalize_builds_reverse_net_connections_and_provenance(tmp_path: Path) -> None:
    schematic_path, symbol_path = _paths(tmp_path)
    result = normalize(
        _schematic(), _symbol(), schematic_path=schematic_path, symbol_path=symbol_path
    )
    cell = result["cells"][0]
    assert result["status"] == "PASS"
    assert cell["ports"] == ["VDD", "EN", "VOUT"]
    assert cell["connections"][:3] == [
        {"instance": "u0", "terminal": "VDD", "net": "VDD"},
        {"instance": "u0", "terminal": "EN", "net": "EN"},
        {"instance": "u0", "terminal": "VOUT", "net": "VOUT"},
    ]
    assert cell["nets"][0]["connections"] == [
        {"instance": "u0", "terminal": "VDD", "net": "VDD"},
        {"instance": None, "terminal": "VDD", "net": "VDD"},
    ]
    assert cell["provenance"]["port_order_source"] == "symbol.port_order"


def test_normalize_excludes_virtuoso_graphical_pin_instances(tmp_path: Path) -> None:
    schematic = _schematic()
    schematic["instances"].append(
        {
            "name": "PIN0",
            "lib": "basic",
            "cell": "ipin",
            "view": "symbol",
            "term_count": 0,
            "terminals": [],
        }
    )
    schematic_path, symbol_path = _paths(tmp_path)

    result = normalize(
        schematic, _symbol(), schematic_path=schematic_path, symbol_path=symbol_path
    )

    cell = result["cells"][0]
    assert [instance["name"] for instance in cell["instances"]] == ["u0"]
    assert cell["provenance"]["ignored_schematic_instances"] == [
        {
            "name": "PIN0",
            "master": "basic/ipin",
            "reason": "virtuoso_top_level_pin_marker",
        }
    ]


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda payload: payload.update(truncated=True), "truncated_inspection"),
        (lambda payload: payload["instances"][0].pop("terminals"), "missing_terminal_connections"),
        (lambda payload: payload["nets"].append(dict(payload["nets"][0])), "duplicate_name"),
        (
            lambda payload: payload["nets"][0].update(instance_terminal_count=99),
            "count_mismatch",
        ),
    ],
)
def test_normalize_fails_closed(mutate, code, tmp_path: Path) -> None:
    schematic = _schematic()
    mutate(schematic)
    schematic_path, symbol_path = _paths(tmp_path)
    with pytest.raises(BlockedInput) as error:
        normalize(schematic, _symbol(), schematic_path=schematic_path, symbol_path=symbol_path)
    assert error.value.code == code


def test_normalize_rejects_symbol_schematic_port_mismatch(tmp_path: Path) -> None:
    schematic_path, symbol_path = _paths(tmp_path)
    symbol = _symbol()
    symbol["terminals"][2]["name"] = "OTHER"
    symbol["port_order"][2] = "OTHER"
    with pytest.raises(BlockedInput, match="terminal set/order"):
        normalize(_schematic(), symbol, schematic_path=schematic_path, symbol_path=symbol_path)


def _run_cli(
    schematic_path: Path,
    symbol_path: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--schematic-json",
            str(schematic_path),
            "--symbol-json",
            str(symbol_path),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_accepts_mcp_tools_call_envelope_and_writes_manifest(tmp_path: Path) -> None:
    schematic_path = tmp_path / "schematic-envelope.json"
    symbol_path = tmp_path / "symbol-envelope.json"
    normalized_path = tmp_path / "out" / "normalized.json"
    manifest_path = tmp_path / "out" / "manifest.json"
    schematic_envelope = {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(_schematic()),
                }
            ],
            "isError": False,
        },
    }
    symbol_envelope = {
        "content": [{"type": "text", "text": json.dumps(_symbol())}],
        "isError": False,
    }
    schematic_path.write_text(json.dumps(schematic_envelope), encoding="utf-8")
    symbol_path.write_text(json.dumps(symbol_envelope), encoding="utf-8")

    completed = _run_cli(
        schematic_path,
        symbol_path,
        "--output",
        str(normalized_path),
        "--manifest-output",
        str(manifest_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "PASS: normalized reg" in completed.stderr
    assert json.loads(normalized_path.read_text(encoding="utf-8"))["status"] == "PASS"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "PASS"
    assert manifest["inputs"]["schematic_json"]["sha256"].startswith("sha256:")


def test_cli_writes_blocked_outputs_for_invalid_json(tmp_path: Path) -> None:
    schematic_path = tmp_path / "bad-schematic.json"
    symbol_path = tmp_path / "symbol.json"
    output_path = tmp_path / "normalized.json"
    manifest_path = tmp_path / "manifest.json"
    schematic_path.write_text("{not-json", encoding="utf-8")
    symbol_path.write_text(json.dumps(_symbol()), encoding="utf-8")

    completed = _run_cli(
        schematic_path,
        symbol_path,
        "--output",
        str(output_path),
        "--manifest-output",
        str(manifest_path),
    )

    assert completed.returncode == 1
    assert completed.stderr.startswith("BLOCKED_INPUT:")
    assert json.loads(output_path.read_text(encoding="utf-8"))["status"] == "BLOCKED_INPUT"
    blocked_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert blocked_manifest["error"]["code"] == "invalid_json"


def test_cli_rejects_output_path_overwriting_input(tmp_path: Path) -> None:
    schematic_path, symbol_path = _paths(tmp_path)

    completed = _run_cli(
        schematic_path,
        symbol_path,
        "--output",
        str(schematic_path),
    )

    assert completed.returncode == 2
    assert "must not overwrite" in completed.stderr


def test_cli_rejects_same_output_and_manifest_path(tmp_path: Path) -> None:
    schematic_path, symbol_path = _paths(tmp_path)
    output_path = tmp_path / "result.json"

    completed = _run_cli(
        schematic_path,
        symbol_path,
        "--output",
        str(output_path),
        "--manifest-output",
        str(output_path),
    )

    assert completed.returncode == 2
    assert "different paths" in completed.stderr


def test_cli_requires_explicit_truncated_false(tmp_path: Path) -> None:
    schematic = _schematic()
    schematic.pop("truncated")
    schematic_path = tmp_path / "schematic.json"
    symbol_path = tmp_path / "symbol.json"
    output_path = tmp_path / "normalized.json"
    schematic_path.write_text(json.dumps(schematic), encoding="utf-8")
    symbol_path.write_text(json.dumps(_symbol()), encoding="utf-8")

    completed = _run_cli(
        schematic_path,
        symbol_path,
        "--output",
        str(output_path),
    )

    assert completed.returncode == 1
    blocked = json.loads(output_path.read_text(encoding="utf-8"))
    assert blocked["error"]["code"] == "truncated_inspection"


def test_cli_rejects_explicit_net_connection_mismatch(tmp_path: Path) -> None:
    schematic = _schematic()
    schematic["nets"][0]["connections"] = [
        {"instance": "u0", "terminal": "VDD", "net": "WRONG"}
    ]
    schematic_path = tmp_path / "schematic.json"
    symbol_path = tmp_path / "symbol.json"
    output_path = tmp_path / "normalized.json"
    schematic_path.write_text(json.dumps(schematic), encoding="utf-8")
    symbol_path.write_text(json.dumps(_symbol()), encoding="utf-8")

    completed = _run_cli(
        schematic_path,
        symbol_path,
        "--output",
        str(output_path),
    )

    assert completed.returncode == 1
    blocked = json.loads(output_path.read_text(encoding="utf-8"))
    assert blocked["error"]["code"] == "connection_mismatch"
