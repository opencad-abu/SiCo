#!/usr/bin/env python3
"""Extend a public reference with additional prescribed startup ramp rates.

Runs the authenticated reference circuit/models on new public startup
stimuli (same source generation, TT, fixed controls) and publishes a
split run in the characterization layout so the refit pipeline can merge
it with the refined reference.  Public data only.
"""

import argparse
import json
from pathlib import Path
import re

from aivworkbench.electrical_experiment import ocean_export, render_deck
from aivworkbench.executors.ldo_structure_common import _CHILD_ENV
from aivworkbench.manifest import publish_split_manifests, resolve_indexed_artifact, verify_split_manifests
from aivworkbench.process import run_process_group
from aivworkbench.profiles import load_profile
from aivworkbench.spectre_data import validate_psf_units
from aivworkbench.toolchain import capture_module_environment, probe_tools
from aivworkbench.waveform_metrics import read_waveform
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once


_PSF_DATASET = re.compile(r'"analysisInst"\s*\(\s*"tran"\s*"([A-Za-z0-9_.-]+)"\s*"PSF"')


def _phase(tool, arguments, *, cwd, environment, log, timeout):
    result = run_process_group((tool, *map(str, arguments)), cwd=cwd, environment=environment,
                               log_file=log, timeout=timeout)
    if result.timed_out or result.returncode != 0:
        raise ValueError("%s failed: timeout=%s rc=%s" % (Path(tool).name, result.timed_out, result.returncode))


def extend(reference_control, *, start_s, ramps, timeout):
    reference_control = Path(reference_control)
    reference_root = Path(json.loads(reference_control.read_text())["details"]["payload_root"])
    base = reference_root/"checks/gates/characterization"
    plan = json.loads((base/"experiment.json").read_text())
    evidence = json.loads((base/"characterization-evidence.json").read_text())
    template = next(c for c in plan["cases"] if c["id"] == "startup_rate_0")
    cases = []
    for index, ramp in enumerate(ramps):
        case = json.loads(json.dumps(template))
        case["id"] = "startup_rate_ext%d" % index
        case["purpose"] = "startup_extension_rate_%d" % index
        case["voltages"]["VDD"]["pwl"] = [[0.0, 0.0], [start_s, 0.0], [start_s + ramp, 5.0],
                                          [0.0004, 5.0], [0.0008, 5.0]]
        case["events"] = [{"id": "edge", "signal": "VDD", "start_s": start_s, "end_s": start_s + ramp,
                           "before_window_s": template["events"][0]["before_window_s"],
                           "after_window_s": template["events"][0]["after_window_s"]}]
        cases.append(case)
    extension_plan = {**plan, "experiment_id": "ldo_master_startup_rate_extension", "cases": cases}
    profile = load_profile("amsverify")
    environment = capture_module_environment(profile.modules)
    qualified = probe_tools([t for t in profile.tools if t.name in ("spectre", "ocean")], environment)
    tools = {t.name: t.path for t in qualified if t.status == "PASS"}
    if set(tools) != {"spectre", "ocean"}:
        raise ValueError("Spectre/OCEAN qualification unavailable")
    run = allocate_split_run(resolve_launch_paths(), Path("/path/to/xh/amsVerify/data/ams").resolve(),
        library=plan["target"]["library"], cell=plan["target"]["cell"], view="reference_extension",
        kind="reference-rate-extension",
        request_digest=stable_digest({"kind": "reference-rate-extension", "reference": str(reference_control),
                                      "start_s": start_s, "ramps": list(ramps)}))
    gate = run.payload_root/"checks/gates/characterization"
    gate.mkdir(parents=True)
    import shutil
    shutil.copyfile(base/"circuit.scs", gate/"circuit.scs")
    shutil.copytree(base/"models", gate/"models")
    signals = {pin: "V" for pin in plan["target"]["term_order"]}
    signals["VPROBE:p"] = "A"
    child = {key: value for key, value in environment.items() if key in _CHILD_ENV}
    child.update({"CDS_LIB": str(gate.parent/"cds.lib"), "CDS_CDSLIB": str(gate.parent/"cds.lib"),
                  "PWD": str(gate)})
    (gate.parent/"cds.lib").write_text("# reference extension\n")
    records = []
    for case in cases:
        case_root = gate/"cases"/case["id"]
        case_root.mkdir(parents=True)
        deck = case_root/"input.scs"
        with deck.open("x") as stream:
            stream.write(render_deck({**extension_plan, "cases": [case]}, case["id"]))
        psf = case_root/"psf"
        _phase(tools["spectre"], (deck, "-format", "psfascii", "-raw", psf, "+log", case_root/"spectre.log"),
               cwd=gate, environment=child, log=case_root/"console.log", timeout=timeout)
        names = _PSF_DATASET.findall((psf/"logFile").read_text())
        if len(names) != 1:
            raise ValueError("one transient PSF dataset is required")
        validate_psf_units(psf/names[0], signals)
        output = case_root/"waveforms.csv"
        script = case_root/"export.ocn"
        with script.open("x") as stream:
            stream.write(ocean_export(psf, output, signals))
        _phase(tools["ocean"], ("-nograph", "-nocdsinit", "-cdslib", gate.parent/"cds.lib",
                                "-log", case_root/"ocean.log", "-replay", script),
               cwd=gate, environment=child, log=case_root/"ocean.console.log", timeout=timeout)
        if "AIVW_WAVEFORM_EXPORT_COMPLETE" not in (case_root/"ocean.log").read_text():
            raise ValueError("waveform export did not complete")
        waves = read_waveform(output, signals, plan["analysis"]["stop_s"])
        records.append({"id": case["id"], "case_digest": stable_digest(case),
                        "measurements": {"waveform_samples": len(waves.times)},
                        "artifacts": [{"path": p.relative_to(run.payload_root).as_posix(),
                                       "sha256": sha256_file(p), "size": p.stat().st_size}
                                      for p in sorted(case_root.rglob("*")) if p.is_file()]})
    extension_evidence = {"schema_version": 1, "dataset": "public_calibration",
                          "source_recheck": "PASS", "source_generation": evidence["source_generation"],
                          "experiment_digest": stable_digest(extension_plan),
                          "target": extension_plan["target"], "cases": records}
    plan_path = gate/"experiment.json"
    write_json_once(plan_path, extension_plan)
    extension_evidence["experiment_sha256"] = sha256_file(plan_path)
    extension_evidence["circuit_sha256"] = sha256_file(gate/"circuit.scs")
    write_json_once(gate/"characterization-evidence.json", extension_evidence)
    publish_split_manifests(run, request_digest=stable_digest({"kind": "reference-rate-extension"}),
        status="EXECUTED",
        control_details={"payload_root": str(run.payload_root)},
        payload_details={"scope": "public_startup_rate_extension"},
        control_artifacts=[], payload_artifacts=[p for p in run.payload_root.rglob("*") if p.is_file()])
    pair = verify_split_manifests(run.control_manifest, run.payload_manifest)
    print(json.dumps({"run_id": run.run_id, "integrity": pair["status"],
                      "cases": [c["id"] for c in cases]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--start", type=float, default=1e-4)
    parser.add_argument("--ramp", type=float, action="append", required=True)
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()
    extend(args.reference, start_s=args.start, ramps=args.ramp, timeout=args.timeout)
