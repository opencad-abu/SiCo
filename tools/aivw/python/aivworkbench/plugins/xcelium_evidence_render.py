"""Render comparator coverage and waveform SystemVerilog instrumentation."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .xcelium_evidence_contract import EvidenceHooks, WAVEFORM_SCOPE
from .xcelium_evidence_routes import qualified_model_parameter_routes
from .xcelium_evidence_validation import point_names


def render_hooks(plan: Mapping[str, Any], *, waveform_scope: str = WAVEFORM_SCOPE) -> EvidenceHooks:
    """Return deterministic SV instrumentation for the canonical L1 plan."""
    coverage = plan.get("coverage")
    waveform = plan.get("waveform")
    matrix = plan.get("matrix")
    if matrix is not None and not isinstance(matrix, Mapping):
        raise ValueError("matrix contract must be an object")
    routes = qualified_model_parameter_routes(plan)
    points = point_names(plan) if isinstance(coverage, Mapping) else []
    point_indices = {name: index for index, name in enumerate(points)}
    declarations = '''
   integer aivw_evidence_failed;
   task automatic aivw_fail(input string message);
      begin
         aivw_evidence_failed = 1;
         $error("AIVW L1 assertion failed: %s", message);
      end
   endtask
'''
    initial_setup_parts = [
        "      aivw_evidence_failed = 0;",
    ]
    initial_sample = ""
    hold_sample = ""
    case_samples: dict[str, str] = {}
    case_event_fields = (
        {
            case_id: {"model_parameter_route": dict(route)}
            for case_id, route in routes["case_routes"].items()
        }
        if routes is not None
        else {}
    )
    before_summary_parts: list[str] = []
    summary_fields: dict[str, Any] = {}
    if isinstance(coverage, Mapping):
        coverage_backend = str(coverage.get("backend", ""))
        bins = "\n".join(
            f"      bins p{index} = {{{index}}};"
            for index in range(len(points))
        )
        declarations += f'''
   integer aivw_coverage_point;
   integer aivw_coverage_seen_count;
   bit aivw_coverage_seen [0:{len(points) - 1}];
   real aivw_coverage_score;
   string aivw_coverage_status;
   string aivw_covered_points_json;
   string aivw_coverage_separator;
   covergroup aivw_l1_coverage;
      cp: coverpoint aivw_coverage_point {{
{bins}
      }}
   endgroup
   aivw_l1_coverage aivw_l1_cov = new();
   task automatic aivw_sample(input integer point_index);
      if (aivw_coverage_seen[point_index] !== 1'b1) begin
         aivw_coverage_seen[point_index] = 1'b1;
         aivw_coverage_seen_count = aivw_coverage_seen_count + 1;
      end
      aivw_coverage_point = point_index;
      aivw_l1_cov.sample();
   endtask
'''
        initial_setup_parts.append("      aivw_coverage_seen_count = 0;")
        initial_setup_parts.extend(
            f"      aivw_coverage_seen[{index}] = 1'b0;"
            for index in range(len(points))
        )
        initial_sample = sample_line(
            point_indices.get("initial-state")
        )
        hold_sample = sample_line(point_indices.get("clock-hold"))
        case_samples = {
            case_id: sample_line(point_indices.get(case_id))
            for case_id in (
                str(item.get("id")) for item in plan.get("cases", [])
            )
        }
        minimum = float(coverage.get("minimum_score", 0.0))
        coverage_prefix = json.dumps(
            {
                "event": "coverage",
                "id": str(coverage.get("id", "")),
                "status": "__AIVW_STATUS__",
                "backend": coverage_backend,
                "score": "__AIVW_SCORE__",
                "covered_points": "__AIVW_POINTS__",
                "covered_point_count": "__AIVW_COUNT__",
                "path": "evidence/coverage",
                "analysis": "raw_xcelium_data_unmerged",
            },
            separators=(",", ":"),
        )
        coverage_prefix = (
            coverage_prefix.replace('"__AIVW_STATUS__"', '"%s"')
            .replace('"__AIVW_SCORE__"', "%0.17g")
            .replace('"__AIVW_POINTS__"', "%s")
            .replace('"__AIVW_COUNT__"', "%0d")
        )
        point_json_parts = [
            "      aivw_covered_points_json = \"[\";",
            "      aivw_coverage_separator = \"\";",
        ]
        for index, point in enumerate(points):
            quoted_point = sv_string(json.dumps(point))
            point_json_parts.extend(
                (
                    f"      if (aivw_coverage_seen[{index}] === 1'b1) begin",
                    "         aivw_covered_points_json = "
                    "{aivw_covered_points_json, aivw_coverage_separator, "
                    f'"{quoted_point}"}};',
                    '         aivw_coverage_separator = ",";',
                    "      end",
                )
            )
        point_json_parts.append(
            '      aivw_covered_points_json = {aivw_covered_points_json, "]"};'
        )
        required_indices = [
            point_indices[str(point)]
            for point in coverage.get("required_points", [])
        ]
        required_condition = " && ".join(
            f"(aivw_coverage_seen[{index}] === 1'b1)"
            for index in required_indices
        ) or "1'b1"
        before_summary_parts.extend(
            (
                "      aivw_coverage_score = aivw_l1_cov.get_inst_coverage() / 100.0;",
                *point_json_parts,
                f"      if ((aivw_coverage_score >= {minimum:.17g}) && "
                f"({required_condition})) begin",
                '         aivw_coverage_status = "PASS";',
                "      end else begin",
                '         aivw_coverage_status = "FAIL";',
                "         aivw_evidence_failed = 1;",
                "      end",
                f"      $display(\"AIVW_L1_EVENT {sv_string(coverage_prefix)}\", "
                "aivw_coverage_status, aivw_coverage_score, "
                "aivw_covered_points_json, aivw_coverage_seen_count);",
            )
        )
        summary_fields["coverage_count"] = 1
    if isinstance(waveform, Mapping):
        retention = waveform.get("retention")
        summary_fields["waveform_count"] = 1 if retention == "always" else 0
        waveform_prefix = json.dumps(
            {
                "event": "waveform",
                "id": str(waveform.get("id", "")),
                "status": "PASS",
                "format": "shm",
                "path": "evidence/waves.shm",
                "producer_mode": "xcelium_runtime",
            },
            separators=(",", ":"),
        )
        if retention == "always":
            before_summary_parts.append(
                f'      $display("AIVW_L1_EVENT {sv_string(waveform_prefix)}");'
            )
        elif retention == "on_failure":
            before_summary_parts.extend(
                (
                    "      if (aivw_evidence_failed) begin",
                    '         $shm_open("evidence/waves.shm", 1);',
                    f'         $shm_probe("AS", {waveform_scope});',
                    f'         $display("AIVW_L1_EVENT {sv_string(waveform_prefix)}");',
                    "         #1;",
                    "         $shm_close();",
                    "      end",
                )
            )
    if isinstance(matrix, Mapping):
        summary_fields["matrix_point_count"] = matrix.get("expected_point_count")
    if routes is not None:
        summary_fields["model_parameter_instance_count"] = routes[
            "instance_count"
        ]
    return EvidenceHooks(
        declarations=declarations,
        initial_setup="\n".join(initial_setup_parts),
        initial_sample=initial_sample,
        hold_sample=hold_sample,
        case_samples=case_samples,
        case_event_fields=case_event_fields,
        before_summary="\n".join(before_summary_parts),
        after_summary=(
            "      if (aivw_evidence_failed) "
            '$fatal(1, "AIVW evidence adapter observed a failed L1 assertion");'
        ),
        failure_action="aivw_fail",
        summary_fields=summary_fields,
    )


def sample_line(index: int | None) -> str:
    if index is None:
        return ""
    return f"      aivw_sample({index});"


def sv_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
