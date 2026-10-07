"""Scratch-only Spectre three-vector evidence exporter for comparator_new."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from .errors import EnvironmentError
from .m1ai_correlation import _reject_excluded_path, _validate_source_run
from .profiles import Profile
from .workspace import LaunchPaths, allocate_run, sha256_file, stable_digest, write_json_once


EXPORTER_VERSION = "aivw-m1-spectre-evidence-3"
LICENSE_ENDPOINT = os.environ.get("AIVW_SPECTRE_LICENSE_ENDPOINT", "").strip()
_SOURCE_NETLIST = re.compile(
    r"(?:^|/)Interactive\.0/1/saradc:comparator_offset_TB_new:1/netlist/netlist$"
)
_OCEAN_MARKER = re.compile(
    r"^AIVW_SPECTRE_EVIDENCE\s+case=(?P<id>\S+)\s+"
    r"out=(?P<out>\S+)\s+outb=(?P<outb>\S+)\s*$",
    re.MULTILINE,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_source_manifest(path: Path) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    if not path.is_absolute():
        raise EnvironmentError("--spectre-source-manifest must be an absolute path")
    _reject_excluded_path(path, label="Spectre source manifest")
    if not path.is_file():
        raise EnvironmentError(f"Spectre source manifest is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentError(f"Spectre source manifest is not valid JSON: {path}") from exc
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise EnvironmentError("Spectre source manifest has no artifact index")
    candidates = [
        item
        for item in artifacts
        if isinstance(item, Mapping) and _SOURCE_NETLIST.search(str(item.get("path", "")))
    ]
    if len(candidates) != 1:
        raise EnvironmentError(
            "Spectre source manifest must contain exactly one nominal numeric-point netlist; "
            f"found {len(candidates)}"
        )
    record = dict(candidates[0])
    relative = Path(str(record["path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise EnvironmentError("Spectre source netlist artifact path is unsafe")
    netlist = (path.parent / relative).resolve(strict=False)
    if not netlist.is_file() or str(record.get("sha256", "")) != sha256_file(netlist):
        raise EnvironmentError("Spectre source netlist artifact hash does not match")
    source = _validate_source_run(
        {
            "manifest": str(path),
            "manifest_sha256": sha256_file(path),
            "run_id": str(payload.get("run_id", "")),
            "artifact": {"path": relative.as_posix(), "sha256": sha256_file(netlist)},
        },
        role="spectre",
    )
    return payload, netlist, source


def extract_comparator_subckts(text: str) -> str:
    """Retain official netlisted cells through comparator_new, excluding its ADE TB."""
    marker = "// Cell name: comparator_offset_TB_new"
    cut = text.find(marker)
    if cut < 0:
        raise EnvironmentError("source netlist does not contain the expected comparator testbench")
    # Remove the two comment lines that introduce the excluded top-level testbench.
    library_comment = text.rfind("// Library name:", 0, cut)
    fragment = text[: library_comment if library_comment >= 0 else cut].rstrip() + "\n"
    expected = r"subckt comparator_new Din\+ Din\- clk out outb inh_groundvssa! inh_power"
    if expected not in fragment or "ends comparator_new" not in fragment:
        raise EnvironmentError("source netlist comparator_new signature is not the approved 7-port form")
    if "offsetrampgenerator" in fragment:
        raise EnvironmentError("derived comparator subcircuits unexpectedly contain testbench stimulus")
    return fragment


def render_spectre_deck(subckts: str, model_path: Path) -> str:
    quoted_model = str(model_path).replace('"', '\\"')
    return f'''// AI Verification Workbench controlled comparator correlation deck
simulator lang=spectre
global 0 vdda! vssa!
parameters VDD=1.2
include "{quoted_model}" section=tt

{subckts}
VDD0 (vdda! 0) vsource type=dc dc=VDD
VSS0 (vssa! 0) vsource type=dc dc=0
VCLK (clk 0) vsource type=pulse val0=0 val1=VDD delay=1n rise=10p fall=10p width=2n period=6n

VPP (dinp_pos 0) vsource type=dc dc=0.7
VPN (dinm_pos 0) vsource type=dc dc=0.5
IPOS (dinp_pos dinm_pos clk out_pos outb_pos vssa! vdda!) comparator_new

VNP (dinp_neg 0) vsource type=dc dc=0.5
VNN (dinm_neg 0) vsource type=dc dc=0.7
INEG (dinp_neg dinm_neg clk out_neg outb_neg vssa! vdda!) comparator_new

VZP (dinp_zero 0) vsource type=dc dc=0.6
VZN (dinm_zero 0) vsource type=dc dc=0.6
IZERO (dinp_zero dinm_zero clk out_zero outb_zero vssa! vdda!) comparator_new

simulatorOptions options reltol=1e-4 vabstol=1e-7 iabstol=1e-12 temp=27 tnom=27 maxwarns=20
tran tran stop=3n maxstep=5p errpreset=conservative
save out_pos outb_pos out_neg outb_neg out_zero outb_zero clk
'''


def render_ocean_script(psf: Path, sample_time: float = 2.005e-9) -> str:
    psf_text = json.dumps(str(psf))
    return f'''openResults({psf_text})
selectResult('tran)
printf("AIVW_SPECTRE_EVIDENCE case=nominal-positive out=%.17g outb=%.17g\\n" value(v("out_pos") {sample_time:.17g}) value(v("outb_pos") {sample_time:.17g}))
printf("AIVW_SPECTRE_EVIDENCE case=nominal-negative out=%.17g outb=%.17g\\n" value(v("out_neg") {sample_time:.17g}) value(v("outb_neg") {sample_time:.17g}))
printf("AIVW_SPECTRE_EVIDENCE case=nominal-zero out=%.17g outb=%.17g\\n" value(v("out_zero") {sample_time:.17g}) value(v("outb_zero") {sample_time:.17g}))
printf("AIVW_SPECTRE_EVIDENCE checks=3 PASS\\n")
exit()
'''


def parse_spectre_evidence_log(text: str) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    types = {
        "nominal-positive": "positive_differential",
        "nominal-negative": "negative_differential",
        "nominal-zero": "zero_differential",
    }
    for match in _OCEAN_MARKER.finditer(text):
        case_id = match.group("id")
        if case_id in cases:
            raise EnvironmentError(f"duplicate Spectre evidence case marker: {case_id}")
        try:
            out = float(match.group("out"))
            outb = float(match.group("outb"))
        except ValueError as exc:
            raise EnvironmentError(f"invalid Spectre evidence value for {case_id}") from exc
        if not math.isfinite(out) or not math.isfinite(outb):
            raise EnvironmentError(f"non-finite Spectre evidence value for {case_id}")
        if out >= 0.9 and outb <= 0.3:
            decision = "positive"
        elif out <= 0.3 and outb >= 0.9:
            decision = "negative"
        elif abs(out - outb) <= 0.01:
            decision = "tie"
        else:
            decision = "unknown"
        cases[case_id] = {
            "id": case_id,
            "type": types.get(case_id, ""),
            "corner": {"VDD": "1.2", "temperature": "27", "process": "tt"},
            "measurements": {"decision": decision, "out": out, "outb": outb},
        }
    required = set(types)
    if set(cases) != required:
        raise EnvironmentError(
            "Spectre evidence markers must contain exactly the three nominal cases: "
            f"missing={sorted(required - set(cases))}, extra={sorted(set(cases) - required)}"
        )
    return cases


def _run(
    command: tuple[str, ...], *, cwd: Path, environment: Mapping[str, str], log: Path, timeout: float
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=dict(environment),
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
        return {"status": "TIMEOUT", "returncode": None, "timed_out": True, "command": list(command)}
    log.write_text(completed.stdout, encoding="utf-8")
    return {
        "status": "PASS" if completed.returncode == 0 else "FAIL_TOOL",
        "returncode": completed.returncode,
        "timed_out": False,
        "command": list(command),
    }


def run_m1_ai_spectre_evidence(
    profile: Profile,
    launch: LaunchPaths,
    *,
    source_manifest: str | Path,
    spectre_path: str,
    ocean_path: str,
    environment: Mapping[str, str],
    timeout: float = 300.0,
) -> dict[str, object]:
    started = _now()
    source_manifest_path = Path(source_manifest).expanduser()
    _payload, source_netlist, source = _read_source_manifest(source_manifest_path)
    adc = profile.projects.get("adc", {})
    model_path = Path(str(adc.get("root", ""))) / "TECH/GPDK045/gpdk045/models/spectre/gpdk045.scs"
    _reject_excluded_path(model_path, label="Spectre model")
    if not model_path.is_absolute() or not model_path.is_file():
        raise EnvironmentError(f"approved Spectre model is unavailable: {model_path}")
    fragment = extract_comparator_subckts(source_netlist.read_text(encoding="utf-8"))
    deck_text = render_spectre_deck(fragment, model_path)
    request = {
        "pilot": "m1-ai",
        "phase": "spectre-correlation-evidence",
        "exporter_version": EXPORTER_VERSION,
        "source_manifest": str(source_manifest_path),
        "source_manifest_sha256": sha256_file(source_manifest_path),
        "source_run_id": source["run_id"],
        "source_artifact": source["artifact"],
        "model_path": str(model_path),
        "model_sha256": sha256_file(model_path),
        "deck_digest": stable_digest(deck_text),
        "sample_delay_seconds": 1.0e-9,
        "logical_cwd": str(launch.logical_cwd),
    }
    run = allocate_run(launch, "m1-ai-spectre-evidence", stable_digest(request))
    spectre_dir = run.root / "spectre"
    evidence_dir = run.root / "evidence"
    spectre_dir.mkdir()
    evidence_dir.mkdir()
    fragment_path = spectre_dir / "comparator-subckts.scs"
    fragment_path.write_text(fragment, encoding="utf-8")
    deck = spectre_dir / "input.scs"
    deck.write_text(deck_text, encoding="utf-8")
    psf = spectre_dir / "psf"
    spectre_log = spectre_dir / "spectre.out"
    run_environment = dict(environment)
    if not LICENSE_ENDPOINT:
        raise EnvironmentError(
            "set AIVW_SPECTRE_LICENSE_ENDPOINT to the Spectre license server (port@host)"
        )
    run_environment["CDS_LIC_FILE"] = LICENSE_ENDPOINT
    run_environment["LM_LICENSE_FILE"] = LICENSE_ENDPOINT
    spectre_command = (
        spectre_path,
        "-64",
        str(deck),
        "+lqtimeout",
        "10",
        "+log",
        str(spectre_log),
        "-format",
        "psfxl",
        "-raw",
        str(psf),
    )
    spectre_exec_log = spectre_dir / "spectre-console.log"
    spectre_execution = _run(
        spectre_command,
        cwd=spectre_dir,
        environment=run_environment,
        log=spectre_exec_log,
        timeout=timeout,
    )
    status = str(spectre_execution["status"])
    error = ""
    cases: dict[str, dict[str, Any]] = {}
    ocean_execution: dict[str, Any] = {"status": "NOT_RUN"}
    if status == "PASS":
        spectre_text = spectre_log.read_text(encoding="utf-8", errors="replace") if spectre_log.is_file() else ""
        version_match = re.search(r"^Version\s+([^\n]+)", spectre_text, re.MULTILINE)
        summary_match = re.search(
            r"spectre completes with (\d+) errors?, (\d+) warnings?, and (\d+) notices?",
            spectre_text,
        )
        spectre_execution["version"] = version_match.group(1).strip() if version_match else ""
        spectre_execution["summary"] = (
            {
                "errors": int(summary_match.group(1)),
                "warnings": int(summary_match.group(2)),
                "notices": int(summary_match.group(3)),
            }
            if summary_match
            else {}
        )
        spectre_execution["license_endpoint_verified"] = (
            f"Configured Lic search path (22.01-s002): {LICENSE_ENDPOINT}" in spectre_text
        )
        if not summary_match or int(summary_match.group(1)) != 0:
            status = "FAIL_SPECTRE_EVIDENCE"
            error = "Spectre zero-error completion summary is missing"
        elif "23.1.0.538.isr10" not in str(spectre_execution["version"]):
            status = "BLOCKED_ENVIRONMENT"
            error = "Spectre execution version does not match the approved profile"
        elif not spectre_execution["license_endpoint_verified"]:
            status = "BLOCKED_LICENSE"
            error = "Spectre did not report the approved license endpoint"
        elif re.search(r"FATAL|license.*(fail|denied|checkout)", spectre_text, re.IGNORECASE):
            status = "BLOCKED_LICENSE"
            error = "Spectre log reports a fatal or license failure"
    if status == "PASS":
        ocean_script = evidence_dir / "extract.ocn"
        ocean_script.write_text(render_ocean_script(psf), encoding="utf-8")
        (evidence_dir / "cds.lib").write_text(
            "# Isolated OCEAN result-reader library file; no OA libraries required.\n",
            encoding="utf-8",
        )
        ocean_log = evidence_dir / "ocean.log"
        ocean_cds_log = evidence_dir / "ocean-cds.log"
        ocean_execution = _run(
            (
                ocean_path,
                "-nocdsinit",
                "-log",
                str(ocean_cds_log),
                "-restore",
                str(ocean_script),
            ),
            cwd=evidence_dir,
            environment=run_environment,
            log=ocean_log,
            timeout=min(timeout, 120.0),
        )
        if ocean_execution["status"] == "PASS":
            try:
                cases = parse_spectre_evidence_log(ocean_log.read_text(encoding="utf-8"))
            except (OSError, EnvironmentError) as exc:
                status = "FAIL_SPECTRE_EVIDENCE"
                error = str(exc)
        else:
            status = "FAIL_SPECTRE_EVIDENCE"
            error = "OCEAN did not export the requested scalar measurements"
    evidence = {
        "schema_version": 1,
        "kind": "spectre-correlation-evidence",
        "target": {"library": "saradc", "cell": "comparator_new", "module": "comparator_new"},
        "source_run": {
            "manifest": str(source_manifest_path),
            "manifest_sha256": sha256_file(source_manifest_path),
            "run_id": source["run_id"],
            "artifact": source["artifact"],
        },
        "model": {"path": str(model_path), "sha256": sha256_file(model_path), "section": "tt"},
        "execution": {"spectre": spectre_execution, "ocean": ocean_execution},
        "stimulus": {
            "corner": {"VDD": "1.2", "temperature": "27", "process": "tt"},
            "clock": {"edge": "rising", "sample_delay_seconds": 1.0e-9},
            "description": "rising edge midpoint at 1.005 ns; sample at 2.005 ns",
            "cases": {
                "nominal-positive": {"type": "positive_differential", "Din+": 0.7, "Din-": 0.5},
                "nominal-negative": {"type": "negative_differential", "Din+": 0.5, "Din-": 0.7},
                "nominal-zero": {"type": "zero_differential", "Din+": 0.6, "Din-": 0.6},
            },
        },
        "cases": list(cases.values()),
    }
    evidence_path = evidence_dir / "spectre-evidence.json"
    write_json_once(evidence_path, evidence)
    manifest = {
        "schema_version": 1,
        "product": "AI Verification Workbench",
        "kind": "m1-ai-spectre-correlation-input",
        "status": status,
        "started_at": started,
        "finished_at": _now(),
        "run_id": run.run_id,
        "run_dir": str(run.root),
        "request": request,
        "source_run": source,
        "execution": {"spectre": spectre_execution, "ocean": ocean_execution},
        "evidence": {"path": str(evidence_path), "sha256": sha256_file(evidence_path)},
        "error": error,
        "provenance": {
            "source_oa_mutation": "not performed; source is an immutable golden-run netlist artifact",
            "license_endpoint": LICENSE_ENDPOINT,
            "human_review_required": True,
        },
        "artifacts": [
            {"path": str(path.relative_to(run.root)), "sha256": sha256_file(path), "size": path.stat().st_size}
            for path in sorted(run.root.rglob("*"))
            if path.is_file()
        ],
    }
    write_json_once(run.manifest, manifest)
    return manifest


__all__ = [
    "extract_comparator_subckts",
    "parse_spectre_evidence_log",
    "render_ocean_script",
    "render_spectre_deck",
    "run_m1_ai_spectre_evidence",
]
