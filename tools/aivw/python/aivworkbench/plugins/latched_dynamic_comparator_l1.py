"""L1 smoke-testbench rendering for the latched comparator."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .latched_dynamic_comparator_model import _sv_name
from .latched_dynamic_comparator_matrix import (
    _case_supply_values,
    _finite_number,
    _matrix_instances,
    _sv_event,
    _sv_string,
)
from .xcelium_evidence import get_xcelium_evidence_adapter


def _render_l1_testbench(
    spec: Mapping[str, Any],
    test_plan: Mapping[str, Any],
    request_options: Mapping[str, Any] | None = None,
) -> str:
    temporal_assertions = test_plan.get("temporal_assertions", [])
    if temporal_assertions:
        raise ValueError(
            "latched_dynamic_comparator does not yet render temporal assertions; "
            "register a model-class temporal binding before enabling them"
        )
    adapter_name = (
        request_options.get("xcelium_evidence_adapter")
        if isinstance(request_options, Mapping)
        else None
    )
    adapter = get_xcelium_evidence_adapter(adapter_name)
    if test_plan.get("coverage") is not None or test_plan.get("waveform") is not None:
        if adapter is None:
            if test_plan.get("coverage") is not None:
                raise ValueError(
                    "latched_dynamic_comparator does not yet render coverage evidence; "
                    "register a qualified Xcelium evidence adapter"
                )
            raise ValueError(
                "latched_dynamic_comparator does not yet retain waveform evidence; "
                "register a qualified Xcelium evidence adapter"
            )
        adapter_findings = adapter.validate_plan(
            test_plan, adapter.binding_for_plan(test_plan)
        )
        if adapter_findings:
            raise ValueError(
                "Xcelium evidence adapter cannot qualify this L1 contract: "
                + json.dumps(adapter_findings, sort_keys=True)
            )
        hooks = adapter.render_hooks(test_plan)
    else:
        hooks = None
    roles = spec["interface"]["roles"]
    parameters = spec["parameters"]
    module = _sv_name(str(spec["target"]["module"]))
    positive_input_name = str(roles["positive_input"])
    negative_input_name = str(roles["negative_input"])
    positive_input = _sv_name(positive_input_name)
    negative_input = _sv_name(negative_input_name)
    positive_output = _sv_name(str(roles["positive_output"]))
    negative_output = _sv_name(str(roles["negative_output"]))
    clock = _sv_name(str(roles["clock"]))
    cases = test_plan["cases"]
    by_id = {item["id"]: item for item in cases}
    hold_case = by_id.get(test_plan["hold_case"])
    if hold_case is None:
        raise ValueError("l1 hold_case does not identify a verification case")
    matrix_instances = _matrix_instances(test_plan, parameters)
    if matrix_instances is None:
        signal_names = {
            "in_p_drive": "in_p_drive",
            "in_n_drive": "in_n_drive",
            "in_p": "in_p",
            "in_n": "in_n",
            "out_p": "out_p",
            "out_n": "out_n",
        }
        dut_declarations = f'''   wreal1driver in_p, in_n;
   wreal1driver out_p, out_n;
   {module} dut(
      .{positive_output} (out_p),
      .{negative_output} (out_n),
      .{positive_input} (in_p),
      .{negative_input} (in_n),
      .{clock} (clk_drive)
   );
   assign in_p = in_p_drive;
   assign in_n = in_n_drive;'''
    else:
        signal_names = {}
        declaration_parts: list[str] = []
        for instance in matrix_instances["instances"]:
            suffix = instance["suffix"]
            names = {
                "in_p_drive": f"in_p_drive_{suffix}",
                "in_n_drive": f"in_n_drive_{suffix}",
                "in_p": f"in_p_{suffix}",
                "in_n": f"in_n_{suffix}",
                "out_p": f"out_p_{suffix}",
                "out_n": f"out_n_{suffix}",
            }
            signal_names[instance["key"]] = names
            override_parts = []
            for parameter_name, value in instance["overrides"].items():
                numeric_value = _finite_number(
                    value, f"{parameter_name} matrix value"
                )
                override_parts.append(f".{parameter_name}({numeric_value:.17g})")
            overrides = ", ".join(override_parts)
            declaration_parts.append(
                f'''   real {names["in_p_drive"]}, {names["in_n_drive"]};
   wreal1driver {names["in_p"]}, {names["in_n"]};
   wreal1driver {names["out_p"]}, {names["out_n"]};
   {module} #({overrides}) {instance["name"]}(
      .{positive_output} ({names["out_p"]}),
      .{negative_output} ({names["out_n"]}),
      .{positive_input} ({names["in_p"]}),
      .{negative_input} ({names["in_n"]}),
      .{clock} (clk_drive)
   );
   assign {names["in_p"]} = {names["in_p_drive"]};
   assign {names["in_n"]} = {names["in_n_drive"]};'''
            )
        dut_declarations = "\n".join(declaration_parts)

    hold_instance_key = matrix_instances["case_keys"].get(hold_case["id"]) if matrix_instances else None
    hold_signals = (
        signal_names[hold_instance_key]
        if hold_instance_key is not None
        else signal_names
    )
    hold_vdd, hold_vss = _case_supply_values(hold_case, matrix_instances, parameters)
    initial_event = _sv_event({"event": "check", "id": "initial-state", "status": "PASS"})
    hold_event = _sv_event({"event": "check", "id": "clock-hold", "status": "PASS"})
    blocks: list[str] = []
    for case in cases:
        inputs = case["inputs"]
        if positive_input_name not in inputs or negative_input_name not in inputs:
            raise ValueError(f"case {case['id']} omits comparator differential inputs")
        in_p = _finite_number(inputs[positive_input_name], f"case {case['id']} positive input")
        in_n = _finite_number(inputs[negative_input_name], f"case {case['id']} negative input")
        metrics = case["metrics"]
        decision = metrics.get("decision", {}).get("expected")
        out_expected = _finite_number(
            metrics.get("out", {}).get("expected"), f"case {case['id']} out"
        )
        outb_expected = _finite_number(
            metrics.get("outb", {}).get("expected"), f"case {case['id']} outb"
        )
        out_tolerance = _finite_number(
            metrics.get("out", {}).get("absolute_tolerance", 0.0),
            f"case {case['id']} out tolerance",
        )
        outb_tolerance = _finite_number(
            metrics.get("outb", {}).get("absolute_tolerance", 0.0),
            f"case {case['id']} outb tolerance",
        )
        instance_key = matrix_instances["case_keys"].get(case["id"]) if matrix_instances else None
        case_signals = (
            signal_names[instance_key]
            if instance_key is not None
            else signal_names
        )
        case_vdd, case_vss = _case_supply_values(case, matrix_instances, parameters)
        assertions = case["required_assertions"]
        required = {"decision", "out", "outb", "complementary"}
        if set(assertions) != required or not isinstance(decision, str):
            raise ValueError(
                f"case {case['id']} must require decision/out/outb/complementary assertions"
            )
        event_payload: dict[str, Any] = {
            "event": "case",
            "id": case["id"],
            "type": case["type"],
            "region": case["region"],
            "status": "PASS",
            "passed_assertions": assertions,
            "metrics": {
                "decision": decision,
                "out": "__AIVW_OUT__",
                "outb": "__AIVW_OUTB__",
            },
        }
        for metadata_key in ("corner", "vector_id"):
            if metadata_key in case:
                event_payload[metadata_key] = case[metadata_key]
        if hooks:
            event_payload.update(hooks.case_event_fields.get(case["id"], {}))
        event_prefix = json.dumps(
            event_payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        # Measured real values come from Xcelium's formatter; every other field
        # is emitted through the structured JSON serializer above.
        event_prefix = event_prefix.replace('"__AIVW_OUT__"', "%0.17g").replace(
            '"__AIVW_OUTB__"', "%0.17g"
        )
        sample_line = hooks.case_samples.get(case["id"], "") if hooks else ""
        failure_action = hooks.failure_action if hooks else None
        out_failure = (
            f'         else {failure_action}("{case["id"]}: out assertion failed");'
            if failure_action
            else f'         else $fatal(1, "{case["id"]}: out assertion failed");'
        )
        outb_failure = (
            f'         else {failure_action}("{case["id"]}: outb assertion failed");'
            if failure_action
            else f'         else $fatal(1, "{case["id"]}: outb assertion failed");'
        )
        decision_failure = (
            f'         else {failure_action}("{case["id"]}: decision assertion failed");'
            if failure_action
            else f'         else $fatal(1, "{case["id"]}: decision assertion failed");'
        )
        complementary_failure = (
            f'         else {failure_action}("{case["id"]}: complementary assertion failed");'
            if failure_action
            else f'         else $fatal(1, "{case["id"]}: complementary assertion failed");'
        )
        blocks.append(
            f'''      {case_signals["in_p_drive"]} = {in_p:.17g}; {case_signals["in_n_drive"]} = {in_n:.17g}; #1;
      clk_drive = 1'b1; #1; clk_drive = 1'b0;
      assert (({case_signals["out_p"]} >= {out_expected - out_tolerance:.17g}) &&
              ({case_signals["out_p"]} <= {out_expected + out_tolerance:.17g}))
{out_failure}
      assert (({case_signals["out_n"]} >= {outb_expected - outb_tolerance:.17g}) &&
              ({case_signals["out_n"]} <= {outb_expected + outb_tolerance:.17g}))
{outb_failure}
      assert (({case_signals["out_p"]} >= {out_expected - out_tolerance:.17g}) &&
              ({case_signals["out_p"]} <= {out_expected + out_tolerance:.17g}) &&
              ({case_signals["out_n"]} >= {outb_expected - outb_tolerance:.17g}) &&
              ({case_signals["out_n"]} <= {outb_expected + outb_tolerance:.17g}))
{decision_failure}
      assert ((({case_signals["out_p"]} + {case_signals["out_n"]}) >= {case_vdd + case_vss - max(out_tolerance, outb_tolerance):.17g}) &&
              (({case_signals["out_p"]} + {case_signals["out_n"]}) <= {case_vdd + case_vss + max(out_tolerance, outb_tolerance):.17g}))
{complementary_failure}
      {sample_line}
      $display("AIVW_L1_EVENT {_sv_string(event_prefix)}", {case_signals["out_p"]}, {case_signals["out_n"]});'''
        )
    hold_inputs = hold_case["inputs"]
    hold_p = _finite_number(hold_inputs[positive_input_name], "hold positive input")
    hold_n = _finite_number(hold_inputs[negative_input_name], "hold negative input")
    summary_fields = {
        "event": "summary",
        "status": "PASS",
        "case_count": len(cases),
        "check_count": len(test_plan["required_checks"]),
        "assertion_count": sum(len(case["required_assertions"]) for case in cases),
    }
    if hooks:
        summary_fields.update(hooks.summary_fields)
    summary = _sv_event(
        summary_fields
    )
    pass_marker = f"AIVW_RNM_SMOKE checks={len(test_plan['required_checks']) + len(cases)} PASS"
    return f'''`timescale 1ns/1ps
module aivw_latched_comparator_l1_tb;
{hooks.declarations if hooks else ""}
   real in_p_drive, in_n_drive;
   logic clk_drive;
{dut_declarations}
   initial begin
{hooks.initial_setup if hooks else ""}
      clk_drive = 1'b0; {hold_signals["in_p_drive"]} = 0.0; {hold_signals["in_n_drive"]} = 0.0;
      #1;
      assert (({hold_signals["out_p"]} >= {hold_vss - 1.0e-9:.17g}) && ({hold_signals["out_p"]} <= {hold_vss + 1.0e-9:.17g}) &&
              ({hold_signals["out_n"]} >= {hold_vdd - 1.0e-9:.17g}) && ({hold_signals["out_n"]} <= {hold_vdd + 1.0e-9:.17g}))
         else {('aivw_fail("initial-state assertion failed");' if hooks else '$fatal(1, "initial-state assertion failed");')}
      $display("AIVW_L1_EVENT {_sv_string(initial_event)}");
{hooks.initial_sample if hooks else ""}
      {hold_signals["in_p_drive"]} = {hold_p:.17g}; {hold_signals["in_n_drive"]} = {hold_n:.17g}; #1;
      assert (({hold_signals["out_p"]} >= {hold_vss - 1.0e-9:.17g}) && ({hold_signals["out_p"]} <= {hold_vss + 1.0e-9:.17g}) &&
              ({hold_signals["out_n"]} >= {hold_vdd - 1.0e-9:.17g}) && ({hold_signals["out_n"]} <= {hold_vdd + 1.0e-9:.17g}))
         else {('aivw_fail("clock-hold assertion failed");' if hooks else '$fatal(1, "clock-hold assertion failed");')}
      $display("AIVW_L1_EVENT {_sv_string(hold_event)}");
{hooks.hold_sample if hooks else ""}
{chr(10).join(blocks)}
{hooks.before_summary if hooks else ""}
      $display("AIVW_L1_EVENT {_sv_string(summary)}");
      $display("{pass_marker}");
{hooks.after_summary if hooks else ""}
      $finish;
   end
endmodule
'''


__all__ = ["_render_l1_testbench"]
