"""Finite-domain DC reference qualification with evaluator-only holdout data.

This harness is separate from the generic recipe runner. Model fitting receives
authenticated public evidence only. It cannot read evaluator cases. The public
report exposes aggregates; detailed vectors/results remain in private payload.
The result qualifies a static slice, never full dynamic or product behavior.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import json
import os
from pathlib import Path
import re
import secrets
import time

from ..acceptance import compare_numeric, compare_repeats
from ..characterization_evidence import read_characterization
from ..electrical_experiment import ocean_export, render_deck
from ..experiment_partition import stratified_points, reserve_holdout, freeze_candidate
from ..manifest import publish_split_manifests, verify_split_manifests
from ..plugins.measured_dc import calibrate_surface, render_surface, render_dc_testbench
from ..process import run_process_group
from ..profiles import load_profile, product_root
from ..spectre_data import measure_csv, validate_psf_units
from ..toolchain import capture_module_environment, probe_tools, parse_setup_exports
from ..workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once
from ..executors.ldo_structure_common import _CHILD_ENV


def _unit_case(case, plan):
    return {"kind":case["kind"], "stimulus":{"voltages":case["voltages"], "load_ohm":case["load_ohm"]},
            "measurement":plan["analysis"]}


def _holdout_cases(plan, domain, seed):
    """Evaluator only. Produce new whole DC cases, including crossed edges."""
    target=domain["targets"][plan["target"]["cell"]]
    lo,hi=target["supply_v"]; rl,rh=target["load_ohm"]
    # One seed determines the complete reservation. Boundary choices also use
    # this seed and are distinct from the public one-factor sweeps.
    import random
    rng=random.Random(seed)
    points=stratified_points({"supply_v":[lo,hi],"load_ohm":[rl,rh]},domain["samples"]["latin_hypercube_interior"],seed=seed)
    for v in (lo,hi):
        points += [{"supply_v":v,"load_ohm":r} for r in (rl,rh)]
        points.append({"supply_v":v,"load_ohm":rl+(rh-rl)*(0.1+0.8*rng.random())})
    for load in (rl,rh):points.append({"supply_v":lo+(hi-lo)*(0.1+0.8*rng.random()),"load_ohm":load})
    roles=plan["roles"]
    result=[]
    for index,point in enumerate(points):
        volts={roles["supply"]:{"dc_v":point["supply_v"]},roles["reference"]:{"dc_v":domain["reference_v"]}}
        volts.update({p:{"dc_v":v} for p,v in target["fixed_controls_v"].items()})
        result.append({"id":"case_%03d"%index,"kind":"dc","purpose":"held_out_dc",
                       "voltages":volts,"load_ohm":point["load_ohm"],"product_check":False})
    return result


def _run(command, cwd, env, log, deadline):
    remaining=deadline-time.monotonic()
    if remaining<=0:raise TimeoutError("qualification deadline exhausted")
    result=run_process_group(command,cwd=cwd,environment=env,log_file=log,timeout=remaining)
    write_json_once(log.with_suffix(".process.json"),result.to_dict())
    if result.timed_out or result.returncode!=0:
        raise RuntimeError("tool did not complete; details retained in evaluator payload")


def _evaluate_repeat(run, plan, cases, candidate, source, env, tools, deadline):
    """No raw values are returned to the public caller/report."""
    root=run.payload_root
    hidden=root/"evaluator-private"
    hidden.mkdir(mode=0o700)
    models=hidden/"models";models.mkdir()
    old_root=Path(source["payload_root"])/"checks/gates/characterization"
    for record in source["evidence"]["model_files"]:
        src=old_root/"models"/record["relative_path"]
        if sha256_file(src)!=record["sha256"]:raise ValueError("model source copy changed")
        dst=models/record["relative_path"];dst.parent.mkdir(parents=True,exist_ok=True)
        with dst.open("xb") as f:f.write(src.read_bytes())
    with (hidden/"circuit.scs").open("xb") as f:f.write(Path(source["circuit"]).read_bytes())
    local_plan=deepcopy(plan);local_plan["cases"]=cases
    write_json_once(hidden/"experiment.json",local_plan)
    signals={p:"V" for p in plan["target"]["term_order"]}; signals["VPROBE:p"]="A"
    output=plan["roles"]["output"]
    expected=[]
    for case in cases:
        case_root=hidden/"cases"/case["id"];case_root.mkdir(parents=True)
        deck=case_root/"input.scs"
        with deck.open("x") as f:f.write(render_deck(local_plan,case["id"]))
        psf=case_root/"psf"
        _run((tools["spectre"],str(deck),"-format","psfascii","-raw",str(psf),"+log",str(case_root/"spectre.log")),case_root,env,case_root/"console.log",deadline)
        log=(case_root/"spectre.log").read_text()
        if not re.search(r"spectre completes with 0 errors",log,re.I) or re.search(r"^\s*(?:ERROR|FATAL)\b",log,re.M):
            raise ValueError("Spectre holdout did not complete cleanly")
        names=re.findall(r'"analysisInst"\s*\(\s*"tran"\s*"([A-Za-z0-9_.-]+)"\s*"PSF"',(psf/"logFile").read_text())
        if len(names)!=1:raise ValueError("missing holdout transient result")
        validate_psf_units(psf/names[0],signals)
        csv_path=case_root/"waveforms.csv"
        script=case_root/"export.ocn"
        with script.open("x") as f:f.write(ocean_export(psf,csv_path,signals))
        _run((tools["ocean"],"-nograph","-nocdsinit","-cdslib",env["CDS_LIB"],"-log",str(case_root/"ocean.log"),"-replay",str(script)),case_root,env,case_root/"ocean.console.log",deadline)
        if "AIVW_WAVEFORM_EXPORT_COMPLETE" not in (case_root/"ocean.log").read_text():raise ValueError("incomplete holdout export")
        measure=measure_csv(csv_path,signals,plan["analysis"]["steady_window_s"],plan["analysis"]["stop_s"])
        expected.append(measure["samples"][-1]["values"][output])
    rnm=hidden/"xcelium";rnm.mkdir()
    model_path=rnm/"candidate.sv"
    with model_path.open("xb") as f:f.write(candidate.read_bytes())
    if sha256_file(model_path)!=sha256_file(candidate):raise ValueError("candidate copy mismatch")
    model=json.loads(candidate.with_name("model.json").read_text())
    tb=rnm/"tb.sv"
    with tb.open("x") as f:f.write(render_dc_testbench(model,cases))
    _run((tools["xrun"],"-64bit","-sv","-top","measured_dc_tb","-xmlibdirname",str(rnm/"worklib"),"-l",str(rnm/"xrun.log"),str(model_path),str(tb)),rnm,env,rnm/"console.log",deadline)
    log=(rnm/"xrun.log").read_text()
    if re.search(r"\*[EF],",log) or "AIVW_MEASURED_DC_COMPLETE" not in log:raise ValueError("Xcelium holdout failed")
    with (rnm/"rnm.csv").open() as f:
        reader=csv.reader(f)
        if next(reader)!=["case","VOUT[V]"]:raise ValueError("RNM CSV unit mismatch")
        rows=list(reader)
    if [r[0] for r in rows]!=[c["id"] for c in cases] or any(len(r)!=2 for r in rows):raise ValueError("RNM holdout coverage mismatch")
    measurements=[compare_numeric(float(row[1]),want,unit="V",tolerance=plan["acceptance"]["correlation"]) for row,want in zip(rows,expected)]
    write_json_once(hidden/"comparisons.json",{"cases":measurements})
    status="PASS" if all(m["status"]=="PASS" for m in measurements) else "FAIL_CORRELATION"
    return {"status":status,"case_count":len(cases),"max_relative_error":max(m["relative_error"] for m in measurements),
            "max_absolute_error_v":max(m["absolute_error"] for m in measurements),
            "measurement_digest":stable_digest(measurements),"candidate_sha256":sha256_file(candidate),
            "xcelium":"PASS","spectre":"PASS"}


def qualify(control_paths, domain_path, *, timeout=900.):
    sources=[read_characterization(Path(p).resolve()) for p in control_paths]
    if len(sources)!=2:raise ValueError("two source characterization repeats are required")
    replay=compare_repeats([s["repeat_record"] for s in sources])
    if replay["status"]!="PASS":raise ValueError("source characterization repeats differ")
    source=sources[0];plan=source["experiment"];cell=plan["target"]["cell"]
    domain=json.loads(Path(domain_path).read_text())
    domain_hash=sha256_file(Path(domain_path))
    if domain["scope"]!="rnm_correlation_only_not_product_supply_window" or domain["repeat_runs"]!=2:
        raise ValueError("unsupported qualification domain scope")
    if domain["correlation"]!=plan["acceptance"]["correlation"] or domain["temperature_c"]!=plan["analysis"]["temperature_c"] or domain["model_section"]!=plan["model"]["section"]:
        raise ValueError("domain differs from characterized PVT or acceptance")
    profile=load_profile("amsverify")
    full_env=capture_module_environment(profile.modules)
    qualified=probe_tools(profile.tools,full_env)
    tools={item.name:item.path for item in qualified if item.status=="PASS"}
    if not {"spectre","xrun","ocean"}.issubset(tools):raise ValueError("required tools are not qualified")
    env={k:v for k,v in full_env.items() if k in _CHILD_ENV}
    env.update({"CDS_LIB":str(profile.projects["ldo"]["cds_lib"]),"CDS_CDSLIB":str(profile.projects["ldo"]["cds_lib"])})
    project_db=Path(parse_setup_exports(profile.setup_script,(profile.storage.payload_env,))[profile.storage.payload_env]).resolve()
    launch=resolve_launch_paths()
    implementation = {name:sha256_file(product_root()/"python/aivworkbench"/name) for name in (
        "qualification/dc_surface.py", "plugins/measured_dc.py", "acceptance.py",
        "experiment_partition.py", "electrical_experiment.py", "characterization_evidence.py")}
    request={"kind":"measured-dc-qualification","target":plan["target"],"domain_sha256":domain_hash,
             "source_runs":[s["repeat_record"]["run_id"] for s in sources],"acceptance_sha256":plan["acceptance"]["decision_sha256"],
             "scope":"dc_only","tool_qualification":[item.to_dict() for item in qualified],"implementation":implementation,"publication":False}
    request_hash=stable_digest(request)
    run=allocate_split_run(launch,project_db,library=plan["target"]["library"],cell=cell,view="rnm_dc_candidate",kind="dc-qualification",request_digest=request_hash)
    root=run.payload_root.resolve()
    # The outer file index contains the private artifacts for auditability.
    # The generation function receives only source evidence and no locators to
    # this evaluator-owned directory. Production MCP access control is separate.
    write_json_once(run.control_root/"request.json",request)
    status="BLOCKED_EVIDENCE";report={"status":status}
    try:
        seed=secrets.randbits(128)
        cases=_holdout_cases(plan,domain,seed)
        commitment=reserve_holdout(root/"reservation",public_cases=[_unit_case(c,plan) for c in plan["cases"]],
                                   hidden_cases=[_unit_case(c,plan) for c in cases],seed=seed,domain_digest=domain_hash,policy_digest=request["acceptance_sha256"])
        write_json_once(root/"holdout-commitment.json",commitment)
        model=calibrate_surface(plan,source["evidence"])
        target_domain=domain["targets"][cell]
        if [model["supply_knots"][0][0],model["supply_knots"][-1][0]]!=target_domain["supply_v"] or model["load_range_ohm"]!=target_domain["load_ohm"] or model["fixed_controls_v"]!=target_domain["fixed_controls_v"]:
            raise ValueError("candidate domain differs from frozen holdout domain")
        candidate_dir=root/"candidate";candidate_dir.mkdir()
        write_json_once(candidate_dir/"model.json",model)
        candidate=candidate_dir/"candidate.sv"
        with candidate.open("x") as f:f.write(render_surface(model))
        frozen=freeze_candidate(root/"reservation",commitment,candidate_digest=sha256_file(candidate),contract_digest=domain_hash)
        write_json_once(root/"candidate-freeze.json",frozen)
        outputs=[];repeat_runs=[];deadline=time.monotonic()+timeout
        for i in range(2):
            child=allocate_split_run(launch,project_db,library=plan["target"]["library"],cell=cell,view="rnm_dc_candidate",kind="dc-holdout-repeat",request_digest=request_hash)
            os.chmod(child.payload_root,0o700)
            write_json_once(child.control_root/"request.json",{**request,"parent_run":run.run_id,"repeat":i+1,"candidate_sha256":sha256_file(candidate),"holdout":commitment})
            if sha256_file(root/"reservation/reservation.json")!=commitment["commitment_sha256"] or sha256_file(candidate)!=frozen["candidate_digest"]:
                raise ValueError("frozen candidate or holdout changed")
            try:
                outcome=_evaluate_repeat(child,plan,cases,candidate,source,env,tools,deadline)
            except Exception as exc:
                failed={"status":"BLOCKED_EVIDENCE","error_type":type(exc).__name__,"reason":str(exc),"qualification":"NOT_ESTABLISHED"}
                write_json_once(child.payload_root/"summary.json",failed)
                publish_split_manifests(child,request_digest=request_hash,status="BLOCKED_EVIDENCE",
                                        control_details={"scope":"dc_correlation_only","parent_run":run.run_id,"payload_root":str(child.payload_root),"summary":failed},
                                        payload_details={"visibility":"evaluator_private","scope":"dc_correlation_only"},
                                        control_artifacts=[child.control_root/"request.json"],payload_artifacts=[p for p in child.payload_root.rglob("*") if p.is_file()])
                raise
            write_json_once(child.payload_root/"summary.json",outcome)
            child_status=outcome["status"]
            publish_split_manifests(child,request_digest=request_hash,status=child_status,
                                    control_details={"scope":"dc_correlation_only","parent_run":run.run_id,"payload_root":str(child.payload_root),"summary":outcome,"holdout":commitment},
                                    payload_details={"visibility":"evaluator_private","scope":"dc_correlation_only"},
                                    control_artifacts=[child.control_root/"request.json"],payload_artifacts=[p for p in child.payload_root.rglob("*") if p.is_file()])
            verify_split_manifests(child.control_manifest,child.payload_manifest)
            outputs.append({"run_id":child.run_id,"control_manifest":str(child.control_manifest),"summary":outcome})
            repeat_runs.append({"run_id":child.run_id,"manifest_status":"PASS","execution_status":"PASS",
                                "inputs":{"candidate":sha256_file(candidate),"source":source["evidence"]["source_generation"],"holdout":commitment,"tools":request["tool_qualification"]},
                                "measurements":{"digest":outcome["measurement_digest"],"status":outcome["status"]}})
        repeated=compare_repeats(repeat_runs)
        # Reverify input manifests after all EDA work. This is qualification of
        # that frozen source generation, not a claim about a newer OA revision.
        for p in control_paths:read_characterization(Path(p).resolve())
        if any(sha256_file(product_root()/"python/aivworkbench"/name)!=digest for name,digest in implementation.items()):
            raise ValueError("qualification implementation changed during execution")
        status="PASS" if repeated["status"]=="PASS" and all(o["summary"]["status"]=="PASS" for o in outputs) else "FAIL_CORRELATION"
        report={"status":status,"qualification":"DC_SLICE_QUALIFIED" if status=="PASS" else "DC_SLICE_FAILED",
                "scope":"static_dc_fixed_enable_resistive_load_tt_27C","full_behavior_qualification":"NOT_ESTABLISHED",
                "product_status":source["evidence"]["product_status"],"product_scope":source["evidence"]["product_scope"],
                "domain_sha256":domain_hash,"source_generation":source["evidence"]["source_generation"],
                "candidate_sha256":sha256_file(candidate),"generation_mode":model["generation_mode"],"ai_generation":model["ai_generation"],
                "holdout":commitment,"repeats":outputs,"repeat_comparison":repeated,
                "source_characterization_repeat":replay,"unsupported":domain["unsupported"],"publication":"NOT_PERFORMED",
                "blindness_scope":"no_holdout_values_in_generation_inputs_or_agent_context; same_user_OS_sandbox_not_claimed"}
    except Exception as exc:
        report={"status":"BLOCKED_EVIDENCE","error_type":type(exc).__name__,"reason":str(exc),"qualification":"NOT_ESTABLISHED"}
        status="BLOCKED_EVIDENCE"
    write_json_once(root/"qualification-summary.json",report)
    write_json_once(run.control_root/"report.json",report)
    publish_split_manifests(run,request_digest=request_hash,status=status,
                            control_details={"payload_root":str(root),"scope":"dc_slice_qualification","summary":report},
                            payload_details={"scope":"dc_slice_qualification"},
                            control_artifacts=[run.control_root/"request.json",run.control_root/"report.json"],
                            payload_artifacts=[p for p in root.rglob("*") if p.is_file()])
    pair=verify_split_manifests(run.control_manifest,run.payload_manifest)
    return {"run_id":run.run_id,"control_manifest":str(run.control_manifest),"payload_root":str(root),"manifest_status":pair["status"],"summary":report}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-control",action="append",required=True)
    parser.add_argument("--domain",type=Path,required=True)
    parser.add_argument("--timeout",type=float,default=900.)
    args=parser.parse_args()
    result=qualify(args.source_control,args.domain,timeout=args.timeout)
    print(json.dumps(result,indent=2))
    return 0 if result["summary"]["status"]=="PASS" else 1


if __name__=="__main__":raise SystemExit(main())
