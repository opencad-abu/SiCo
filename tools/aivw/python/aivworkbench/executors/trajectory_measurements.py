"""Registered dynamic measurement gate; no product timing/current verdict."""

from __future__ import annotations

from ..executor import ExecutorResult
from ..trajectory_evidence import public_trajectories
from ..waveform_metrics import event_measurements, window_statistics
from ..workspace import sha256_file, stable_digest, write_json_once


def run_trajectory_measurements(context):
    try:
        plan, evidence, waves = public_trajectories(context)
        output = plan["roles"]["output"]
        records = []
        for case in plan["cases"]:
            wave = waves[case["id"]]
            records.append({"id": case["id"], "stimulus_digest": stable_digest(case),
                "events": [event_measurements(wave, output, "VPROBE:p", event, plan["measurement"])
                           for event in case["events"]],
                "supply_current": window_statistics(wave, "VPROBE:p", [0., plan["analysis"]["stop_s"]])})
        report = {"schema_version": 1, "kind": "dynamic-trajectory-measurements", "status": "OBSERVED",
                  "source_generation": evidence["source_generation"], "experiment_digest": stable_digest(plan),
                  "definition": plan["measurement"], "current_sign": "positive_from_supply_into_block",
                  "cases": records, "product_current_timing_status": "BLOCKED_CONTRACT",
                  "qualification": "NOT_ESTABLISHED"}
        path = context.gate_root/"measurements.json"
        write_json_once(path, report)
        return ExecutorResult("PASS", {"case_count": len(records), "measurement_status": "OBSERVED",
                                      "product_current_timing_status": "BLOCKED_CONTRACT"},
                              {"dynamic_measurements": str(path.relative_to(context.run.payload_root)),
                               "dynamic_measurements_sha256": sha256_file(path),
                               "source_generation": evidence["source_generation"]}, (path,))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return ExecutorResult("BLOCKED_EVIDENCE", {"code": "dynamic_measurement_invalid", "reason": str(exc)})
