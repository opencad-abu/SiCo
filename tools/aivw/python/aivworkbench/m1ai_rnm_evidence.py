"""Produce paired, machine-readable RNM comparator measurements with Xcelium."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from .errors import EnvironmentError, WorkspaceError
from .manifest import resolve_indexed_artifact
from .m1ai_correlation import _validate_source_run, _reject_excluded_path
from .profiles import Profile
from .workspace import LaunchPaths, allocate_run, sha256_file, stable_digest, write_json_once


MODEL_ARTIFACT = "ai-generation/comparator_new.rnm.sv"
MODEL_VERSION = "aivw-m1-rnm-baseline-1"
EXPORTER_VERSION = "aivw-m1-rnm-evidence-2"
_MARKER = re.compile(
    r"AIVW_RNM_EVIDENCE\s+case=(?P<id>\S+)\s+"
    r"decision=(?P<decision>\S+)\s+out=(?P<out>\S+)\s+"
    r"outb=(?P<outb>\S+)"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _render_evidence_tb() -> str:
    return r'''`timescale 1ns/1ps
module comparator_new_evidence_tb;
   real din_p_drive, din_n_drive;
   wreal1driver din_p, din_n;
   wreal1driver out, outb;
   logic clk;
   comparator_new dut(out, outb, din_p, din_n, clk);
   assign din_p = din_p_drive;
   assign din_n = din_n_drive;

   task automatic sample_case(input string id, input string decision,
                              input real p, input real n);
      begin
         din_p_drive = p;
         din_n_drive = n;
         #1;
         clk = 1'b1;
         #1;
         $display("AIVW_RNM_EVIDENCE case=%s decision=%s out=%0.17g outb=%0.17g",
                  id, decision, out, outb);
         clk = 1'b0;
         #1;
      end
   endtask

   initial begin
      clk = 1'b0;
      din_p_drive = 0.0;
      din_n_drive = 0.0;
      #1;
      sample_case("nominal-positive", "positive", 0.7, 0.5);
      sample_case("nominal-negative", "negative", 0.5, 0.7);
      sample_case("nominal-zero", "tie", 0.6, 0.6);
      $display("AIVW_RNM_EVIDENCE checks=3 PASS");
      $finish;
   end
endmodule
'''


def parse_rnm_evidence_log(text: str) -> dict[str, dict[str, Any]]:
    """Parse only the exporter markers; reject duplicates and non-finite values."""
    cases: dict[str, dict[str, Any]] = {}
    for match in _MARKER.finditer(text):
        case_id = match.group("id")
        if case_id in cases:
            raise EnvironmentError(f"duplicate RNM evidence case marker: {case_id}")
        try:
            out = float(match.group("out"))
            outb = float(match.group("outb"))
        except ValueError as exc:
            raise EnvironmentError(f"invalid RNM evidence value for {case_id}") from exc
        import math

        if not math.isfinite(out) or not math.isfinite(outb):
            raise EnvironmentError(f"non-finite RNM evidence value for {case_id}")
        cases[case_id] = {
            "id": case_id,
            "type": {
                "nominal-positive": "positive_differential",
                "nominal-negative": "negative_differential",
                "nominal-zero": "zero_differential",
            }.get(case_id, ""),
            "corner": {"VDD": "1.2", "temperature": "27", "process": "tt"},
            "measurements": {
                "decision": match.group("decision"),
                "out": out,
                "outb": outb,
            },
        }
    required = {"nominal-positive", "nominal-negative", "nominal-zero"}
    if set(cases) != required:
        raise EnvironmentError(
            "RNM evidence markers must contain exactly the three nominal cases: "
            f"missing={sorted(required - set(cases))}, extra={sorted(set(cases) - required)}"
        )
    return cases


def _run_xcelium(
    xrun: str, *, model: Path, tb: Path, work: Path, timeout: float
) -> dict[str, Any]:
    xmlib = work / "xcelium.d"
    xmlib.mkdir(parents=True, exist_ok=True)
    log = work / "xrun-evidence.log"
    command = (xrun, "-sv", "-nolog", "-xmlibdirpath", str(xmlib), str(model), str(tb))
    try:
        completed = subprocess.run(
            command,
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        log.write_text(output, encoding="utf-8")
        return {
            "status": "TIMEOUT",
            "returncode": None,
            "timed_out": True,
            "command": list(command),
        }
    log.write_text(completed.stdout, encoding="utf-8")
    marker_pass = "AIVW_RNM_EVIDENCE checks=3 PASS" in completed.stdout
    return {
        "status": "PASS" if completed.returncode == 0 and marker_pass else "FAIL_RNM_EVIDENCE",
        "returncode": completed.returncode,
        "timed_out": False,
        "command": list(command),
        "pass_marker": marker_pass,
        "version": _xrun_version(xrun, work),
    }


def _xrun_version(xrun: str, work: Path) -> str:
    try:
        completed = subprocess.run(
            (xrun, "-version"),
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}"
    return completed.stdout.strip()


def run_m1_ai_rnm_evidence(
    profile: Profile,
    launch: LaunchPaths,
    *,
    source_manifest: str | Path,
    xrun_path: str,
    timeout: float = 300.0,
) -> dict[str, object]:
    """Run a fixed three-vector RNM measurement in an isolated work directory."""
    del profile
    source_manifest_path = Path(source_manifest).expanduser()
    _reject_excluded_path(source_manifest_path, label="RNM source manifest")
    if not source_manifest_path.is_absolute():
        raise EnvironmentError("--rnm-source-manifest must be an absolute path")
    if not source_manifest_path.is_file():
        raise EnvironmentError(f"RNM source manifest is missing: {source_manifest_path}")
    try:
        manifest_payload = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentError(f"RNM source manifest is not valid JSON: {source_manifest_path}") from exc
    source_run_id = str(manifest_payload.get("run_id", ""))
    try:
        resolved_artifact = resolve_indexed_artifact(source_manifest_path, MODEL_ARTIFACT)
    except WorkspaceError as exc:
        raise EnvironmentError(f"RNM source model artifact is invalid: {exc}") from exc
    source_artifact = resolved_artifact.path
    source_artifact_hash = resolved_artifact.sha256
    source = _validate_source_run(
        {
            "manifest": str(source_manifest_path),
            "manifest_sha256": sha256_file(source_manifest_path),
            "run_id": source_run_id,
            "artifact": {"path": MODEL_ARTIFACT, "sha256": source_artifact_hash},
        },
        role="rnm",
    )
    rendered_tb = _render_evidence_tb()
    request = {
        "pilot": "m1-ai",
        "phase": "rnm-correlation-evidence",
        "source_manifest": str(source_manifest_path),
        "source_manifest_sha256": sha256_file(source_manifest_path),
        "source_run_id": source_run_id,
        "source_artifact": MODEL_ARTIFACT,
        "source_artifact_sha256": source_artifact_hash,
        "model_version": MODEL_VERSION,
        "exporter_version": EXPORTER_VERSION,
        "testbench_digest": stable_digest(rendered_tb),
        "logical_cwd": str(launch.logical_cwd),
    }
    run = allocate_run(launch, "m1-ai-rnm-evidence", stable_digest(request))
    model_dir = run.root / "model"
    tb_dir = run.root / "tb"
    evidence_dir = run.root / "evidence"
    model_dir.mkdir()
    tb_dir.mkdir()
    evidence_dir.mkdir()
    model = model_dir / "comparator_new.rnm.sv"
    shutil.copy2(source_artifact, model)
    tb = tb_dir / "comparator_new_evidence_tb.sv"
    tb.write_text(rendered_tb, encoding="utf-8")
    execution = _run_xcelium(xrun_path, model=model, tb=tb, work=tb_dir, timeout=timeout)
    status = str(execution["status"])
    error = ""
    cases: dict[str, dict[str, Any]] = {}
    if status == "PASS":
        try:
            log_text = (tb_dir / "xrun-evidence.log").read_text(encoding="utf-8")
            cases = parse_rnm_evidence_log(log_text)
        except (OSError, EnvironmentError) as exc:
            status = "FAIL_RNM_EVIDENCE"
            error = str(exc)
    evidence = {
        "schema_version": 1,
        "kind": "rnm-correlation-evidence",
        "target": {"library": "saradc", "cell": "comparator_new", "module": "comparator_new"},
        "source_run": {
            "manifest": str(source_manifest_path),
            "manifest_sha256": sha256_file(source_manifest_path),
            "run_id": source_run_id,
            "artifact": {"path": MODEL_ARTIFACT, "sha256": source_artifact_hash},
        },
        "model": {"version": MODEL_VERSION, "sha256": sha256_file(model)},
        "execution": execution,
        "stimulus": {
            "corner": {"VDD": "1.2", "temperature": "27", "process": "tt"},
            "clock": {"edge": "rising", "sample_delay_seconds": 1.0e-9},
            "description": "sample 1 ns after posedge clk",
            "cases": {
                "nominal-positive": {"type": "positive_differential", "Din+": 0.7, "Din-": 0.5},
                "nominal-negative": {"type": "negative_differential", "Din+": 0.5, "Din-": 0.7},
                "nominal-zero": {"type": "zero_differential", "Din+": 0.6, "Din-": 0.6},
            },
        },
        "cases": list(cases.values()),
    }
    evidence_path = evidence_dir / "rnm-evidence.json"
    write_json_once(evidence_path, evidence)
    manifest = {
        "schema_version": 1,
        "product": "AI Verification Workbench",
        "kind": "m1-ai-rnm-correlation-input",
        "status": status,
        "started_at": _now(),
        "finished_at": _now(),
        "run_id": run.run_id,
        "run_dir": str(run.root),
        "request": request,
        "source_run": source,
        "execution": execution,
        "evidence": {"path": str(evidence_path), "sha256": sha256_file(evidence_path)},
        "error": error,
        "provenance": {
            "source_oa_mutation": "not performed",
            "spectre_correlation": "not performed",
            "human_review_required": True,
        },
        "artifacts": [
            {
                "path": str(path.relative_to(run.root)),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
            for path in sorted(run.root.rglob("*"))
            if path.is_file()
        ],
    }
    write_json_once(run.manifest, manifest)
    return manifest


__all__ = ["parse_rnm_evidence_log", "run_m1_ai_rnm_evidence"]
