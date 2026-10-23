#!/usr/bin/env python3
"""Qualify a sampled-model adapter's network mechanics in real Xcelium.

Underlying transistor correlation and complete AMS substitution are separate.
Only grounded resistor loads and a finite-resistance supply are exercised.
"""

import argparse
import csv
import json
from pathlib import Path
import re

from aivworkbench.manifest import publish_split_manifests, resolve_indexed_artifact, verify_split_manifests
from aivworkbench.plugins.eenet_adapter import render_adapter, render_network_testbench
from aivworkbench.process import run_process_group
from aivworkbench.profiles import load_profile
from aivworkbench.toolchain import capture_module_environment, probe_tools
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once
from aivworkbench.executors.ldo_structure_common import _CHILD_ENV


def qualify(control_path, nominal):
    control = json.loads(control_path.read_text())
    old_root = Path(control["details"]["payload_root"])
    pair = verify_split_manifests(control_path, old_root/"payload-manifest.json")
    gates = [g for g in control["details"]["gate_results"].values() if g["executor"] == "electrical.identify_dynamics"]
    if len(gates) != 1 or gates[0]["status"] != "PASS":
        raise ValueError("one identified candidate required")
    output = gates[0]["outputs"]
    candidate = resolve_indexed_artifact(control_path, output["model_source"]).path
    model_path = resolve_indexed_artifact(control_path, output["dynamic_model"]).path
    if sha256_file(candidate) != output["model_source_sha256"] or sha256_file(model_path) != output["dynamic_model_sha256"]:
        raise ValueError("candidate producer hash differs")
    model = json.loads(model_path.read_text())
    package = Path("/software/eda/cadence/installs/XCELIUM23.09.002/tools.lnx86/affirma_ams/etc/dms/EE_pkg.sv")
    profile = load_profile("amsverify")
    env = capture_module_environment(profile.modules)
    qualified = probe_tools([s for s in profile.tools if s.name == "xrun"], env)
    if len(qualified) != 1 or qualified[0].status != "PASS":
        raise ValueError("qualified Xcelium unavailable")
    request = {"kind": "eenet-adapter-mechanics", "candidate_source_sha256": sha256_file(candidate),
               "source_generation": model["source_generation"], "source_pair": pair,
               "package_sha256": sha256_file(package), "nominal_supply_v": nominal,
               "scope": "grounded_resistor_load_finite_supply_resistance_only"}
    digest = stable_digest(request)
    run = allocate_split_run(resolve_launch_paths(), Path("/path/to/xh/amsVerify/data/ams").resolve(),
        library="aivw", cell=model["module"], view="eenet_adapter", kind="eenet-adapter", request_digest=digest)
    write_json_once(run.control_root/"request.json", request)
    root = run.payload_root
    (root/"candidate.sv").write_bytes(candidate.read_bytes())
    (root/"model.json").write_bytes(model_path.read_bytes())
    (root/"adapter.sv").write_text(render_adapter(model))
    (root/"EE_pkg.sv").write_bytes(package.read_bytes())
    summary = {"status": "FAIL_ADAPTER", "qualification": "NOT_ESTABLISHED"}
    try:
        checks = []
        for kind, expected in (("passive_network", None), ("outside_load", "EENET_LOAD_OUTSIDE_DOMAIN"),
                               ("active_load", "EENET_ACTIVE_LOAD_UNSUPPORTED"),
                               ("ideal_driver", "EENET_OUTPUT_DRIVER_CONFLICT")):
            negative = expected is not None
            local = root/kind
            local.mkdir()
            tb = local/"tb.sv"
            tb.write_text(render_network_testbench(model, nominal, invalid_load=kind == "outside_load", negative_case=kind))
            process = run_process_group((qualified[0].path, "-64bit", "-sv", "-top", "eenet_tb", "-xmlibdirname",
                str(local/"worklib"), "-l", str(local/"xrun.log"), str(root/"EE_pkg.sv"), str(root/"candidate.sv"),
                str(root/"adapter.sv"), str(tb)), cwd=local, environment={k:v for k,v in env.items() if k in _CHILD_ENV},
                log_file=local/"console.log", timeout=120.)
            write_json_once(local/"process.json", process.to_dict())
            log = (local/"xrun.log").read_text(errors="replace")
            if negative:
                ok = process.returncode != 0 and not process.timed_out and expected in log
                checks.append({"case": kind, "status": "PASS" if ok else "FAIL"})
            else:
                ok = process.returncode == 0 and not process.timed_out and "AIVW_EENET_ADAPTER_COMPLETE" in log and not re.search(r"\*[EF],", log)
                if not ok:
                    raise ValueError("network execution failed; inspect isolated xrun.log")
                with (local/"network.csv").open() as stream:
                    rows = list(csv.DictReader(stream))
                error = max(abs(float(row["kcl[A]"])) for row in rows)
                if len(rows) != 300 or error > 1e-9:
                    raise ValueError("network sample/KCL contract failed")
                checks.append({"case": "passive_network", "status": "PASS", "samples": len(rows),
                    "max_kcl_error_a": error, "min_supply_droop_v": min(nominal-float(r["rail[V]"]) for r in rows)})
        if any(c["status"] != "PASS" for c in checks):
            raise ValueError("network negative check failed")
        summary = {"status": "PASS_ADAPTER_MECHANICS", "checks": checks, "candidate_sha256": sha256_file(candidate),
                   "source_generation": model["source_generation"], "adapter_sha256": sha256_file(root/"adapter.sv"),
                   "package_sha256": sha256_file(package), "scope": request["scope"],
                   "dynamic_qualification": "NOT_ESTABLISHED", "system_substitution": "NOT_QUALIFIED",
                   "oa_config_write": "NOT_PERFORMED", "publication": "NOT_PERFORMED"}
        verify_split_manifests(control_path, old_root/"payload-manifest.json")
    except Exception as exc:
        summary["reason"] = str(exc)
        raise
    finally:
        write_json_once(root/"summary.json", summary)
        publish_split_manifests(run, request_digest=digest, status=summary["status"],
            control_details={"payload_root": str(root), **summary}, payload_details=summary,
            control_artifacts=[run.control_root/"request.json"], payload_artifacts=[p for p in root.rglob("*") if p.is_file()])
        verify_split_manifests(run.control_manifest, run.payload_manifest)
        print(json.dumps({"run_id": run.run_id, **summary}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--nominal-supply", type=float, required=True)
    args = parser.parse_args()
    qualify(args.manifest.resolve(), args.nominal_supply)
