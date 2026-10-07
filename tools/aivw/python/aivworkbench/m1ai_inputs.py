"""Parse and qualify the M1 comparator Maestro/interface evidence."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from urllib.parse import quote
import xml.etree.ElementTree as ET


def _direct_text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def _named_values(parent: ET.Element | None, item_tag: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if parent is None:
        return values
    for item in parent.findall(item_tag):
        name = _direct_text(item)
        value = _direct_text(item.find("value"))
        if name:
            values[name] = value
    return values


def parse_maestro(path: Path) -> dict[str, object]:
    root = ET.parse(path).getroot()
    active = root.find("active")
    if active is None:
        raise ValueError(f"Maestro setup has no active setup: {path}")

    corners: dict[str, dict[str, object]] = {}
    corner_root = active.find("corners")
    if corner_root is not None:
        for corner in corner_root.findall("corner"):
            name = _direct_text(corner)
            variables = _named_values(corner.find("vars"), "var")
            process: list[str] = []
            models = corner.find("models")
            if models is not None:
                for model in models.findall("model"):
                    section = _direct_text(model.find("modelsection"))
                    process.extend(value.strip('"') for value in section.split())
            corners[name] = {
                "enabled": corner.get("enabled") == "1",
                "variables": variables,
                "process": process,
            }

    tests: dict[str, dict[str, object]] = {}
    test_root = active.find("tests")
    if test_root is not None:
        for test in test_root.findall("test"):
            name = _direct_text(test)
            options = _named_values(test.find("tooloptions"), "option")
            variables = _named_values(test.find("vars"), "var")
            outputs = [
                _direct_text(output)
                for output in test.findall("./outputs/output")
                if _direct_text(output)
            ]
            tests[name] = {
                "enabled": test.get("enabled", "1") == "1",
                "options": options,
                "variables": variables,
                "outputs": outputs,
            }

    specifications: dict[str, dict[str, str]] = {}
    spec_root = active.find("specs")
    if spec_root is not None:
        for spec in spec_root.findall("spec"):
            name = _direct_text(spec)
            specifications[name] = {
                "test": _direct_text(spec.find("testname")),
                "result": _direct_text(spec.find("resname")),
                "type": _direct_text(spec.find("specType")),
                "minimum": _direct_text(spec.find("min")),
                "maximum": _direct_text(spec.find("max")),
                "target": _direct_text(spec.find("target")),
                "tolerance": _direct_text(spec.find("tol")),
            }
    return {
        "schema_version": 1,
        "setupdb_version": root.get("version", ""),
        "tests": tests,
        "corners": corners,
        "specifications": specifications,
    }


def parse_history_rdb(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"exists": False, "path": str(path), "tests": {}, "specifications": {}}
    uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        tests: dict[str, dict[str, object]] = {}
        for test_id, name in connection.execute("SELECT testID,name FROM test"):
            results = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM result WHERE testID=? ORDER BY resultOrder", (test_id,)
                )
            ]
            statuses = [
                {"status_code": row[0], "message": row[1], "count": row[2]}
                for row in connection.execute(
                    "SELECT s.statusCode,e.message,count(*) "
                    "FROM testStatus s LEFT JOIN error e ON e.errorID=s.errorID "
                    "WHERE s.testID=? GROUP BY s.statusCode,e.message",
                    (test_id,),
                )
            ]
            tests[str(name)] = {"results": results, "statuses": statuses}
        specifications: dict[str, dict[str, str]] = {}
        for name, kind, expression1, expression2 in connection.execute(
            "SELECT r.name,s.type,s.expression1,s.expression2 "
            "FROM spec s JOIN result r ON r.resultID=s.resultID"
        ):
            spec_type = str(kind)
            first, second = str(expression1 or ""), str(expression2 or "")
            if spec_type in {"lt", "le"}:
                minimum, maximum = "", first
            elif spec_type in {"gt", "ge"}:
                minimum, maximum = first, ""
            else:
                minimum, maximum = first, second
            specifications[str(name)] = {
                "type": spec_type,
                "minimum": minimum,
                "maximum": maximum,
            }
        point_count = int(connection.execute("SELECT count(*) FROM point").fetchone()[0])
        return {
            "exists": True,
            "path": str(path),
            "point_count": point_count,
            "tests": tests,
            "specifications": specifications,
            "role": "historical_reference_only",
        }
    finally:
        connection.close()


def _cell_key(item: Mapping[str, object]) -> str:
    return "/".join(str(item.get(name, "")) for name in ("library", "cell", "view"))


def _canonical_cell(item: Mapping[str, Any]) -> dict[str, object]:
    terminals = sorted(
        (
            {"name": str(term.get("name", "")), "direction": str(term.get("direction", ""))}
            for term in item.get("terminals", [])
        ),
        key=lambda value: (value["name"], value["direction"]),
    )
    instances: list[dict[str, object]] = []
    for instance in item.get("instances", []):
        connections = sorted(
            (
                {"terminal": str(conn.get("terminal", "")), "net": str(conn.get("net", ""))}
                for conn in instance.get("connections", [])
            ),
            key=lambda value: (value["terminal"], value["net"]),
        )
        instances.append(
            {
                "name": str(instance.get("name", "")),
                "library": str(instance.get("library", "")),
                "cell": str(instance.get("cell", "")),
                "view": str(instance.get("view", "")),
                "connections": connections,
            }
        )
    instances.sort(key=lambda value: str(value["name"]))
    return {
        "terminals": terminals,
        "port_order": [str(value) for value in item.get("port_order", [])],
        "instances": instances,
    }


def _split_values(value: object) -> list[str]:
    return str(value or "").split()


def qualify_inputs(
    contract: Mapping[str, Any],
    maestro: Mapping[str, Any],
    catalog: Mapping[str, Any],
    history: Mapping[str, Any],
    *,
    manual_sha256: str,
) -> tuple[dict[str, object], dict[str, object]]:
    cells = {_cell_key(item): item for item in catalog.get("cellviews", [])}
    target_schematic = cells.get("saradc/comparator_new/schematic", {})
    target_symbol = cells.get("saradc/comparator_new/symbol", {})
    old_schematic = cells.get("saradc/comparator/schematic", {})
    old_symbol = cells.get("saradc/comparator/symbol", {})
    old_ii_schematic = cells.get("saradcII/comparator/schematic", {})
    old_ii_symbol = cells.get("saradcII/comparator/symbol", {})

    expected_ports = sorted(contract["interface"]["ports"], key=lambda value: value["name"])
    actual_ports = sorted(target_symbol.get("terminals", []), key=lambda value: value["name"])
    expected_order = contract["interface"]["port_order"]
    checks: dict[str, bool] = {
        "manual_hash_matches": manual_sha256 == contract["manual_evidence"]["sha256"],
        "catalog_is_read_only_dbaccess": (
            catalog.get("provider") == "dbAccess" and catalog.get("read_only") is True
        ),
        "all_required_cellviews_opened": all(
            bool(cells.get(key, {}).get("opened"))
            for key in (
                "saradc/comparator/schematic",
                "saradc/comparator/symbol",
                "saradcII/comparator/schematic",
                "saradcII/comparator/symbol",
                "saradc/comparator_new/schematic",
                "saradc/comparator_new/symbol",
                "saradc/comparator_noise_TB_new/schematic",
                "saradc/comparator_offset_TB_new/schematic",
            )
        ),
        "target_ports_match_contract": actual_ports == expected_ports,
        "target_port_order_matches_contract": target_symbol.get("port_order") == expected_order,
        "target_and_reference_schematic_structure_match": (
            _canonical_cell(target_schematic) == _canonical_cell(old_schematic)
        ),
        "target_and_reference_symbol_interface_match": (
            _canonical_cell(target_symbol) == _canonical_cell(old_symbol)
        ),
        "reference_library_copies_match": (
            _canonical_cell(old_schematic) == _canonical_cell(old_ii_schematic)
            and _canonical_cell(old_symbol) == _canonical_cell(old_ii_symbol)
        ),
    }

    live_tests = maestro.get("tests", {})
    history_tests = history.get("tests", {})
    binding_evidence: dict[str, object] = {}
    tests_ok = True
    outputs_ok = True
    for test_name, expected in contract["maestro"]["tests"].items():
        live = live_tests.get(test_name, {})
        testbench = str(expected["testbench"]).split("/")[1]
        instance_name = str(expected["current_instance"])
        tb = cells.get(f"saradc/{testbench}/schematic", {})
        instance = next(
            (item for item in tb.get("instances", []) if item.get("name") == instance_name),
            {},
        )
        actual_dut = f"{instance.get('library', '')}/{instance.get('cell', '')}"
        required_outputs = set(expected["required_outputs"])
        available_outputs = set(live.get("outputs", [])) | set(
            history_tests.get(test_name, {}).get("results", [])
        )
        test_valid = bool(live) and live.get("enabled") is True
        binding_valid = actual_dut == expected["current_dut"]
        output_valid = required_outputs <= available_outputs
        tests_ok = tests_ok and test_valid and binding_valid
        outputs_ok = outputs_ok and output_valid
        binding_evidence[test_name] = {
            "test_present_and_enabled": test_valid,
            "testbench": expected["testbench"],
            "instance": instance_name,
            "expected_current_dut": expected["current_dut"],
            "actual_current_dut": actual_dut,
            "current_binding_matches": binding_valid,
            "required_outputs": sorted(required_outputs),
            "available_outputs": sorted(available_outputs),
            "outputs_available": output_valid,
            "required_rebind_target": contract["qualification_policy"]["rebind_target"],
        }
    checks["required_tests_and_current_bindings_match"] = tests_ok
    checks["required_measurements_are_available"] = outputs_ok

    live_specs = maestro.get("specifications", {})
    specs_ok = True
    spec_evidence: dict[str, object] = {}
    for name, expected in contract["maestro"]["specifications"].items():
        actual = live_specs.get(name, {})
        match = all(str(actual.get(key, "")) == str(value) for key, value in expected.items())
        specs_ok = specs_ok and match
        spec_evidence[name] = {"expected": expected, "actual": actual, "matches": match}
    checks["block_specifications_match"] = specs_ok

    expected_corners = contract["maestro"]["corners"]
    corner = maestro.get("corners", {}).get("C1", {})
    corner_variables = corner.get("variables", {})
    temperature = _split_values(corner_variables.get("temperature"))
    supply = _split_values(corner_variables.get("VDD"))
    process = list(corner.get("process", []))
    matrix_size = len(temperature) * len(supply) * len(process)
    checks["corner_matrix_matches"] = (
        corner.get("enabled") is True
        and temperature == expected_corners["temperature"]
        and supply == expected_corners["VDD"]
        and process == expected_corners["process"]
        and matrix_size == expected_corners["matrix_size"]
    )

    canonical = {
        "schema_version": 1,
        "target": contract["target"],
        "interface": {
            "ports": actual_ports,
            "port_order": target_symbol.get("port_order", []),
            "implicit_supplies": contract["interface"]["implicit_supplies"],
        },
        "reference_schematic_structure": _canonical_cell(old_schematic),
        "target_schematic_structure": _canonical_cell(target_schematic),
        "tests": binding_evidence,
        "specifications": spec_evidence,
        "corners": {
            "temperature": temperature,
            "VDD": supply,
            "process": process,
            "matrix_size": matrix_size,
        },
        "manual_evidence": contract["manual_evidence"],
        "rebind_required": True,
        "rebind_target": contract["qualification_policy"]["rebind_target"],
    }
    evidence = {
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "ready_for_isolated_rebinding": all(checks.values()),
        "historical_results_role": "reference_only_not_comparator_new_golden",
        "qualification_scope": "input/interface/spec/corner qualification only",
    }
    return canonical, evidence


def load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError(f"unsupported comparator contract schema: {path}")
    return payload


__all__ = [
    "load_contract",
    "parse_history_rdb",
    "parse_maestro",
    "qualify_inputs",
]
