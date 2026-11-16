from __future__ import annotations

from pathlib import Path

import pytest

from aivworkbench.connectivity import (
    ConnectivityParseError,
    Structure,
    compare_structures,
    parse_globalmap,
    parse_globalmap_models,
    parse_inherited_connections,
    parse_si_map,
    parse_si_netlists,
    parse_verilog,
)
from aivworkbench.executor import ExecutorContext, ExecutorResult
from aivworkbench.executors.connectivity import run_connectivity_check
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, stable_digest


RUN_ROOT = Path(
    "/workarea/xh/amsVerify/data/ams/saradc.comparator_new.rnm/runs/"
    "20260830T100439.525801Z-recipe-run-cf4754c043ba/checks/gates/structure/run"
)


def _official() -> Structure:
    texts = [path.read_text() for path in sorted((RUN_ROOT / "ihnl").glob("cds*/netlist"))]
    aliases = list(parse_si_map((RUN_ROOT / "map/current").read_text()))
    aliases.extend(parse_si_map((RUN_ROOT / "ihnl/cds5/map").read_text()))
    aliases = tuple(sorted(set(aliases)))
    globals_ = parse_globalmap((RUN_ROOT / "ihnl/globalmap").read_text())
    return Structure(parse_si_netlists(texts).modules, globals=globals_, aliases=aliases)


def test_real_si_inventory_matches_itself() -> None:
    official = _official()
    findings = compare_structures(
        official,
        official,
        expected_module="comparator_new",
        expected_ports=("out", "outb", "cdsNet1", "cdsNet0", "clk"),
        expected_globals=official.globals,
    )
    assert findings == ()


def test_real_si_binding_and_inherited_connection_metadata_parse() -> None:
    models = parse_globalmap_models((RUN_ROOT / "ihnl/globalmap").read_text())
    assert ("saradc/comparator_new/schematic", "comparator_new") in models
    inherited = parse_inherited_connections((RUN_ROOT / ".InheritConnInfo").read_text())
    assert ("power", "vdda!", "saradc", "comparator_new", "schematic") in inherited


def test_wrong_connection_and_port_order_are_reported() -> None:
    source = """module comparator_new (out, outb, cdsNet1, cdsNet0, clk);
output out, outb; input cdsNet1, cdsNet0, clk;
latch_buffer I0 (net5, net4, cdsNet1, cdsNet0, net_13);
srlatch_nand I1 (out, outb, net4, net5);
clockdriver I11 (net_13, cds_globals.vdda_, cds_globals.vssa_, clk);
endmodule"""
    candidate = Structure(parse_verilog(source), globals=("vdda!", "cds_globals.vdda_"),)
    official = _official()
    findings = compare_structures(
        official,
        candidate,
        expected_module="comparator_new",
        expected_ports=("out", "outb", "cdsNet1", "cdsNet0", "clk"),
        expected_globals=official.globals,
    )
    codes = {item.code for item in findings}
    assert "connection_mismatch" in codes
    assert "global_mapping_missing" in codes

    reordered = source.replace(
        "(out, outb, cdsNet1, cdsNet0, clk)", "(outb, out, cdsNet1, cdsNet0, clk)"
    )
    reordered_findings = compare_structures(
        official,
        Structure(parse_verilog(reordered), globals=official.globals),
        expected_module="comparator_new",
        expected_ports=("out", "outb", "cdsNet1", "cdsNet0", "clk"),
        expected_globals=official.globals,
    )
    assert any(item.code == "port_order_mismatch" for item in reordered_findings)


def test_recipe_spelling_is_normalized_through_si_aliases() -> None:
    official = _official()
    source = """module comparator_new (out, outb, Din+, Din-, clk);
output out, outb;
input Din+, Din-, clk;
latch_buffer I0 (net5, net4, cdsNet0, cdsNet1, net_13);
srlatch_nand I1 (outb, out, net4, net5);
clockdriver I11 (net_13, cds_globals.vdda_, cds_globals.vssa_, clk);
endmodule"""
    findings = compare_structures(
        official,
        Structure(parse_verilog(source), globals=official.globals),
        expected_module="comparator_new",
        expected_ports=("out", "outb", "cdsNet1", "cdsNet0", "clk"),
        expected_globals=official.globals,
    )
    assert not any(item.code == "port_order_mismatch" for item in findings)
    assert not any(item.code == "connection_mismatch" for item in findings)


def test_missing_instance_and_bus_slice_mismatch_are_reported() -> None:
    official_source = """module top (input logic [3:0] din, output logic y);
leaf I0 (.a(din[2]), .y(y));
endmodule
module leaf (input logic a, output logic y); endmodule"""
    candidate_source = """module top (input logic [3:0] din, output logic y);
leaf I0 (.a(din[1]), .y(y));
endmodule
module leaf (input logic a, output logic y); endmodule"""
    official = Structure(parse_verilog(official_source))
    candidate = Structure(parse_verilog(candidate_source))
    findings = compare_structures(
        official,
        candidate,
        expected_module="top",
        expected_ports=("din", "y"),
    )
    assert any(
        item.code == "connection_mismatch"
        and item.expected == "din[2]"
        and item.actual == "din[1]"
        for item in findings
    )

    missing = Structure(parse_verilog(candidate_source.replace("leaf I0 (.a(din[1]), .y(y));", "")))
    missing_findings = compare_structures(
        official,
        missing,
        expected_module="top",
        expected_ports=("din", "y"),
    )
    assert any(item.code == "missing_instance" for item in missing_findings)


def test_malformed_maps_fail_closed() -> None:
    with pytest.raises(ConnectivityParseError, match="no net section"):
        parse_si_map("( nil )\n")
    with pytest.raises(ConnectivityParseError, match="no net section"):
        parse_globalmap("$model\nlib/top/schematic top\n$endmodel\n")
    with pytest.raises(ConnectivityParseError, match="conflicting targets"):
        parse_si_map('( ( net\n( "a" "x" )\n( "a" "y" )\n )\n )\n')


def test_multiple_output_drivers_are_reported() -> None:
    source = """module top (input logic a, output logic y);
leaf I0 (.a(a), .y(y));
leaf I1 (.a(a), .y(y));
endmodule
module leaf (input logic a, output logic y); endmodule"""
    structure = Structure(parse_verilog(source))
    findings = compare_structures(
        structure,
        structure,
        expected_module="top",
        expected_ports=("a", "y"),
    )
    assert any(item.code == "multiple_drivers" and item.actual == 2 for item in findings)


def test_port_direction_mismatch_is_reported() -> None:
    official = Structure(
        parse_verilog("module top (input logic a, output logic y); endmodule")
    )
    candidate = Structure(
        parse_verilog("module top (output logic a, output logic y); endmodule")
    )
    findings = compare_structures(
        official,
        candidate,
        expected_module="top",
        expected_ports=("a", "y"),
    )
    assert any(
        item.code == "port_direction_mismatch"
        and item.terminal == "a"
        and item.expected == "input"
        and item.actual == "output"
        for item in findings
    )


def test_executor_fails_closed_for_missing_source_and_unsafe_path(tmp_path: Path) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    (tmp_path / "launch").mkdir()
    launch = resolve_launch_paths(tmp_path / "launch")
    run = allocate_split_run(
        launch,
        tmp_path / "payload",
        library="lib",
        cell="cell",
        view="rnm",
        kind="connectivity",
        request_digest=stable_digest({"test": "connectivity"}),
    )
    structure = ExecutorResult(
        "PASS",
        outputs={
            "structure_authoritative": True,
            "source_generation": "a" * 64,
            "netlist": [],
            "map": "map/current",
            "globalmap": "ihnl/globalmap",
        },
    )
    generation = ExecutorResult("PASS", outputs={"connectivity_source": "../escape.sv"})
    context = ExecutorContext(
        recipe=recipe,
        run=run,
        gate_id="connectivity",
        executor_name="connectivity.check",
        dependencies={"structure": structure, "generate": generation},
        environment={},
        tools={},
        timeout=30,
    )
    result = run_connectivity_check(context)
    assert result.status == "BLOCKED_INPUT"
    assert result.summary["code"] == "connectivity_input_invalid"


def test_executor_blocks_when_structure_is_not_authoritative(tmp_path: Path) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    (tmp_path / "launch").mkdir()
    launch = resolve_launch_paths(tmp_path / "launch")
    run = allocate_split_run(
        launch,
        tmp_path / "payload",
        library="lib",
        cell="cell",
        view="rnm",
        kind="connectivity",
        request_digest=stable_digest({"test": "dependency"}),
    )
    context = ExecutorContext(
        recipe=recipe,
        run=run,
        gate_id="connectivity",
        executor_name="connectivity.check",
        dependencies={
            "structure": ExecutorResult("PASS", outputs={"structure_authoritative": False}),
        },
        environment={},
        tools={},
        timeout=30,
    )
    result = run_connectivity_check(context)
    assert result.status == "BLOCKED_INPUT"
    assert result.summary["code"] == "structure_not_authoritative"
