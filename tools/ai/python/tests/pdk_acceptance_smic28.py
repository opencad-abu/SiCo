"""Phase 5 acceptance: recollect smic28hkmg from the live session + install tree.

Stages:
  1. capture - a real Virtuoso session (CDS.library + our SKILL) writes one JSON per
               operation (libraries / categories / directory batches / every device).
  2. publish - the production pipeline (PdkPreparation) replays those captures through
               its bounded batches, gates and workspace database (no session needed).
  3. verify  - A1..A10 checks against the frozen reference dataset ``pdk-data-v3``
               (content comparison; the reference is a yardstick, never a data source).

Usage:
    python3 tests/pdk_acceptance_smic28.py                 # capture once, then verify
    python3 tests/pdk_acceptance_smic28.py --skip-capture  # reuse an existing capture dir
    python3 tests/pdk_acceptance_smic28.py --report out.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PDK_ROOT = Path(os.environ.get(
    "SICO_AI_SMIC28_PDK",
    "/workarea/xh/smic28/smic28hkmg_0925_1P10M_8Ma_8Ic_2TMc_ALPA1_oa_cds_2015_04_15_v1.0"))
LIBRARY = os.environ.get("SICO_AI_SMIC28_LIBRARY", "smic28hkmg")
V3 = Path(os.environ.get("SICO_AI_PDK_V3",
                         "/workarea/xh/smic28/cad/data/pdk-data-v3/libraries/smic28hkmg"))
DEFAULT_WORK = Path(os.environ.get("SICO_AI_ACCEPTANCE_DIR", "/tmp/pdk-acceptance-smic28"))


def log(message):
    print(message, flush=True)


def skill_script(capture_dir: Path) -> str:
    skill = ROOT / "skill"
    modules = ["AI_protocol.il", "AI_eval.il", "AI_pdk_core.il", "AI_pdk_init.il", "AI_pdk_cdf.il",
               "AI_pdk_db.il", "AI_pdk_capture.il"]
    lines = ['load(%s)' % json.dumps(str(skill / name)) for name in modules]
    lines += [
        'ddSetForcedLib(%s)' % json.dumps(str(capture_dir / "cds.lib")),
        'lib=ddGetObj(%s)' % json.dumps(LIBRARY),
        'unless(lib error("acceptance: library did not resolve"))',
        'before=dbGetOpenCellViews()',
        'aiPdkCapture("acc" "session:acceptance" "libraries" nil nil nil)',
        'aiWriteTextFile("libraries.json" aiPdkTransfers["acc"]) aiPdkDrop("acc")',
        'aiPdkCapture("acc" "session:acceptance" "categories" %s nil nil)' % json.dumps(LIBRARY),
        'aiWriteTextFile("categories.json" aiPdkTransfers["acc"]) aiPdkDrop("acc")',
        'total=length(lib~>cells)',
        'for(offset 0 total-1 32 '
        'aiPdkCapture("acc" "session:acceptance" "directory" %s nil "symbol" offset 32) '
        'aiWriteTextFile(strcat("directory-" sprintf(nil "%%d" offset) ".json") '
        'aiPdkTransfers["acc"]) aiPdkDrop("acc"))' % json.dumps(LIBRARY),
        'foreach(cellObj lib~>cells '
        'when(ddGetObj(%s cellObj~>name "symbol") '
        'aiPdkCapture("acc" "session:acceptance" "device" %s cellObj~>name "symbol") '
        'aiWriteTextFile(strcat("device-" cellObj~>name ".json") aiPdkTransfers["acc"]) '
        'aiPdkDrop("acc")))' % (json.dumps(LIBRARY), json.dumps(LIBRARY)),
        'when(aiPdkTransferCount!=0 error("acceptance: leaked transfer"))',
        'unless(equal(before dbGetOpenCellViews()) '
        'error("acceptance: capture leaked a database reference"))',
        'aiWriteTextFile("capture.txt" "ok")',
        'exit()',
    ]
    return "\n".join(lines) + "\n"


def capture(capture_dir: Path, timeout=3600):
    binary = shutil.which(os.environ.get("SICO_AI_VIRTUOSO", "virtuoso"))
    if binary is None:
        raise SystemExit("virtuoso binary unavailable; cannot run the live acceptance capture")
    if not (PDK_ROOT / LIBRARY).is_dir():
        raise SystemExit("PDK library missing: " + str(PDK_ROOT / LIBRARY))
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "cds.lib").write_text(
        "DEFINE analogLib $CDSHOME/tools/dfII/etc/cdslib/artist/analogLib\n"
        "DEFINE basic $CDSHOME/tools/dfII/etc/cdslib/basic\n"
        "DEFINE %s %s\n" % (LIBRARY, PDK_ROOT / LIBRARY))
    script = capture_dir / "capture.il"
    script.write_text(skill_script(capture_dir))
    log("capture: launching virtuoso (real PDK session)")
    started = time.time()
    run = subprocess.run([binary, "-nographE", "-nocdsinit", "-log", str(capture_dir / "CDS.log"),
                          "-restore", str(script)], cwd=str(capture_dir), text=True,
                         capture_output=True, timeout=timeout)
    elapsed = time.time() - started
    output = run.stdout + run.stderr + ((capture_dir / "CDS.log").read_text(errors="replace")
                                        if (capture_dir / "CDS.log").exists() else "")
    if run.returncode != 0 or not (capture_dir / "capture.txt").exists():
        raise SystemExit("capture failed (%s):\n%s" % (run.returncode, output[-4000:]))
    if "*Error*" in output:
        raise SystemExit("capture reported errors:\n" + output[-4000:])
    devices = len(list(capture_dir.glob("device-*.json")))
    batches = len(list(capture_dir.glob("directory-*.json")))
    log("capture: %.1fs, %d device captures, %d directory batches" % (elapsed, devices, batches))
    return elapsed


class ReplayBridge:
    """Serves the production pipeline from recorded session captures (no live process)."""

    def __init__(self, capture_dir: Path):
        self.capture_dir = capture_dir
        self.calls = []

    def call(self, expression):
        """The replay has no live Virtuoso; report the library as initialized."""
        self.calls.append(("call", expression))
        return {"ok": True, "library": LIBRARY, "status": "initialized",
                "initialized": True, "cached": True}

    def _read(self, name):
        path = self.capture_dir / name
        if not path.exists():
            raise RuntimeError("capture missing: " + str(path))
        return json.loads(path.read_text())

    def capture(self, operation, target):
        self.calls.append((operation, deepcopy(target)))
        if operation == "libraries":
            return self._read("libraries.json")
        if operation == "categories":
            return self._read("categories.json")
        if operation == "directory":
            return self._read("directory-%d.json" % target.get("offset", 0))
        if operation == "device":
            return self._read("device-%s.json" % target["cell"])
        raise RuntimeError("unsupported operation " + str(operation))


def publish(capture_dir: Path, workspace: Path):
    from cadai.pdk_preparation import PdkPreparation

    bridge = ReplayBridge(capture_dir)
    preparation = PdkPreparation(bridge, workspace=str(workspace), environment={})
    started = time.time()
    job = preparation.prepare({"library": LIBRARY, "choice_confirmed": True})
    batches = 0
    while job.get("status") == "collecting":
        job = preparation.advance({k: job[k] for k in ("collection_ref", "next_batch")})
        batches += 1
        if batches > 400:
            raise SystemExit("collection did not finish: " + json.dumps(job)[:800])
    return bridge, preparation, job, batches, time.time() - started


def device_files(root: Path):
    return sorted((root / ".sico" / "ai" / "pdk-data" / "devices").glob("*.json"))


def load_devices(root: Path):
    values = []
    for path in device_files(root):
        values.append(json.loads(path.read_text())["device"])
    return {value["target"]["cell"]: value for value in values}


def v3_devices():
    result = {}
    for path in sorted((V3 / "devices").glob("*/*/facts.json")):
        facts = json.loads(path.read_text())
        result[facts["target"]["cell"]] = facts
    return result


def check(report, name, ok, detail):
    report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
    log("%-26s %s  %s" % (name, "PASS" if ok else "FAIL", detail))


def verify(capture_dir: Path, workspace: Path, report):
    from cadai.pdk_session import PdkSession

    bridge, preparation, job, batches, elapsed = publish(capture_dir, workspace)
    published = json.loads(Path(job["path"]).read_text())
    payload = published["payload"]
    devices = load_devices(workspace)
    reference = v3_devices()
    quality = payload.get("quality") or {}
    coverage = payload.get("coverage") or {}
    registry = preparation.jobs[job["collection_ref"]].get("categories") or {}

    report["publish"] = {"batches": batches, "seconds": round(elapsed, 2),
                         "devices": len(devices), "path": job["path"], "coverage": coverage,
                         "quality": {k: quality.get(k) for k in
                                     ("included_devices", "excluded_count", "excluded_reasons",
                                      "registry_cells", "directory_cells", "registry_unclassified")}}

    same_set = set(devices) == set(reference)
    revisions = {cell: value["revision"] for cell, value in devices.items()}
    reference_revisions = {cell: facts["revision"] for cell, facts in reference.items()}
    matches = [cell for cell in revisions if revisions[cell] == reference_revisions.get(cell)]
    check(report, "A1.device_set", same_set,
          "%d collected vs %d reference" % (len(devices), len(reference)))
    check(report, "A1.revisions", len(matches) == len(revisions) == len(reference_revisions),
          "%d/%d device revisions identical" % (len(matches), len(revisions)))
    if len(matches) != len(revisions):
        differ = sorted(set(revisions) - set(matches))[:5]
        report["revision_diff"] = {cell: [revisions[cell], reference_revisions.get(cell)]
                                   for cell in differ}

    cross = registry.get("cross_check") or {}
    check(report, "A2.category_missing", coverage.get("category_missing") == 0,
          "category_missing=%s categories=%s" % (coverage.get("category_missing"),
                                                 coverage.get("categories")))
    check(report, "A2.registry",
          registry.get("status") == "complete" and bool(registry.get("ddcat_cells"))
          and bool(cross.get("registry_cells")),
          "runtime registry=%s (%s cells, status %s), .Cat=%s cells, file fallback=%s cells, "
          "cross-check consistent=%s" % (
              len(registry.get("categories") or {}), cross.get("registry_cells"),
              registry.get("status"), cross.get("file_cells"),
              len(registry.get("file_fallback_cells") or []), cross.get("consistent")))
    report["categories"] = {"runtime": sorted(registry.get("categories") or {}),
                            "children": sorted(registry.get("children") or {}),
                            "file_only": list(cross.get("file_only") or []),
                            "unassigned": list(registry.get("unassigned") or [])}
    mismatched = [cell for cell in devices
                  if sorted(devices[cell]["categories"]["all"]) !=
                  sorted((reference.get(cell, {}).get("categories") or {}).get("all") or [])]
    check(report, "A2.reference_categories", not mismatched,
          "%d category mismatches vs reference" % len(mismatched))

    no_decks = [cell for cell, value in devices.items()
                if not value.get("model_decks") or value["model_resolution"]["status"] != "found"]
    documented = [cell for cell, value in devices.items()
                  if (value.get("limits") or {}).get("documented_minima")]
    check(report, "A3.model_decks", not no_decks,
          "%d devices without a resolved model deck" % len(no_decks))
    check(report, "A3.documented_minima", len(documented) >= 30,
          "%d devices carry documented minima" % len(documented))

    bad_tier = []
    for cell, value in devices.items():
        interface = (value.get("parameter_tiers") or {}).get("interface") or []
        items = value["parameters"]["items"]
        if not interface or "model" not in interface:
            bad_tier.append((cell, "interface/model"))
        elif any(not isinstance(item.get("tier_reason"), list) for item in items):
            bad_tier.append((cell, "tier_reason_type"))
        elif any(item.get("tier") == "interface" and not item.get("tier_reason")
                 for item in items):
            bad_tier.append((cell, "interface_reason_empty"))
    check(report, "A4.tiers", not bad_tier, "tier problems: %s" % (bad_tier[:4] or "none"))

    second = workspace.parent / (workspace.name + "-repeat")
    _, _, job2, _, _ = publish(capture_dir, second)
    repeat = load_devices(second)
    stable = (sorted(repeat) == sorted(devices) and
              all(repeat[cell]["revision"] == devices[cell]["revision"] for cell in devices))
    digests_stable = (sorted(path.name for path in device_files(second)) ==
                      sorted(path.name for path in device_files(workspace)))
    check(report, "A5.repeat_revision", stable, "repeat collection revisions identical")
    check(report, "A5.repeat_digest", digests_stable,
          "repeat device digests identical (%d files)" % len(device_files(second)))
    check(report, "A5.retry_budget", job.get("retry_count", 0) == 0,
          "retry_count=%s batches=%s" % (job.get("retry_count", 0), batches))

    policies = {item.get("execution_policy") for value in devices.values()
                for item in value["callbacks"]["items"]}
    check(report, "A6.callbacks", policies <= {"never_execute_during_discovery"},
          "callback policies: %s" % sorted(policies))
    check(report, "A6.no_oa_writes",
          all(value["master_state"]["modified"] is False for value in devices.values()),
          "saved masters only; capture asserted open-view and transfer cleanup")

    session = PdkSession(bridge=bridge, workspace=str(workspace), environment={})
    cells = sorted(devices)
    served, live_reads = 0, 0
    for cell in cells[:8]:
        search = session.call("search_pdk_devices", {"library": LIBRARY, "cell": cell})
        if not search.get("items"):
            continue
        ref = search["items"][0]["device_ref"]
        snapshot = search["context"]["snapshot_ref"]
        before = len([call for call in bridge.calls if call[0] == "device"])
        detail = session.call("get_pdk_device", {"snapshot_ref": snapshot, "device_ref": ref})
        live_reads += len([call for call in bridge.calls if call[0] == "device"]) - before
        served += 1 if detail["cache_status"] == "persisted_database" else 0
    check(report, "A7.prepared_reads", served == min(8, len(cells)) and live_reads == 0,
          "%d/%d indexed devices served from the prepared database (live reads: %d)"
          % (served, min(8, len(cells)), live_reads))

    from cadai import pdk_files
    from cadai.pdk_data import binding as pdk_binding
    from cadai.pdk_normalize import context as pdk_context
    file_categories = pdk_files.categories_from_files(
        pdk_files.install_root(str(PDK_ROOT / LIBRARY)), LIBRARY)
    reference_excluded = set(file_categories["cells"]) - set(reference)
    reasons = dict(quality.get("excluded_reasons") or {})
    excluded_cells = {row["cell"] for row in quality.get("excluded_devices") or []}
    split_ok = (reasons.get("model_not_in_decks") == 1 and reasons.get("registry_stale") == 12
                and reasons.get("no_symbol_view") == 5)
    check(report, "A8.excluded_split", split_ok and quality.get("excluded_count") == 18,
          "excluded_count=%s reasons=%s" % (quality.get("excluded_count"), reasons))
    check(report, "A8.excluded_set", not reference_excluded or excluded_cells == reference_excluded,
          "excluded cells match the reference accounting (%d cells)" % len(excluded_cells))

    binding_row = payload.get("binding") or {}
    current_revision = re.search(r'aiPdkRevision\(\) "([^"]+)"',
                                (ROOT / "skill" / "AI_pdk_core.il").read_text()).group(1)
    stamps_ok = (binding_row.get("collector_revision") == current_revision
                 and bool(binding_row.get("path_stamp")) and bool(binding_row.get("technology_stamp"))
                 and any(op == "categories" for op, _ in bridge.calls))
    live = json.loads((capture_dir / "libraries.json").read_text())
    ctx = pdk_context(live["context"])
    # Stamps are observation times by design, so the identity (library dict) is what is
    # comparable here; the stamps themselves are checked for presence above.
    context_ok = pdk_binding(ctx, LIBRARY)["library"] == binding_row["library"]
    check(report, "A9.provenance", stamps_ok and context_ok,
          "collector revision %s, library identity and source stamps recorded" % (
              binding_row.get("collector_revision"),))
    check(report, "A9.file_layer",
          all((value.get("file_layer") or {}).get("available")
              and value["limits"].get("index_digest") for value in devices.values()),
          "install-directory index digest recorded for every device")

    search = session.call("search_pdk_devices", {"library": LIBRARY, "cell": "n09_ckt"})
    if search.get("items"):
        ref = search["items"][0]["device_ref"]
        snapshot = search["context"]["snapshot_ref"]
        interface = session.call("get_pdk_device", {"snapshot_ref": snapshot, "device_ref": ref})
        full = session.call("get_pdk_device", {"snapshot_ref": snapshot, "device_ref": ref,
                                               "tier": "all"})
        count_interface = interface["device"]["parameters"]["count"]
        count_all = full["device"]["parameters"]["count"]
        names = [item["name"] for item in interface["device"]["parameters"]["items"]]
        size = len(json.dumps(interface, ensure_ascii=False, indent=2).encode())
        check(report, "A10.tier_default",
              30 <= count_interface <= 40 and 150 <= count_all <= 165 and "model" in names,
              "interface=%d all=%d model=%s bytes=%d" % (count_interface, count_all,
                                                         "model" in names, size))
        check(report, "A10.response_budget", size <= 90_000,
              "interface response %d bytes <= 90000" % size)
        check(report, "A10.interface_view",
              interface["device"]["callbacks"]["count"] <= 40 and size <= 55_000,
              "interface projection: %s of %s callbacks, %d bytes (raw/lists via tier=all)" % (
                  interface["device"]["callbacks"]["count"],
                  interface["device"]["callbacks"]["all_count"], size))
        report["a10"] = {"interface": count_interface, "all": count_all, "bytes": size}
    else:
        check(report, "A10.tier_default", False, "n09_ckt not searchable")

    report["ok"] = all(item["ok"] for item in report["checks"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-dir", default=str(DEFAULT_WORK / "captures"))
    parser.add_argument("--workspace", default=str(DEFAULT_WORK / "workspace"))
    parser.add_argument("--skip-capture", action="store_true")
    parser.add_argument("--capture-only", action="store_true")
    parser.add_argument("--report", default=str(DEFAULT_WORK / "acceptance-report.json"))
    args = parser.parse_args(argv)
    capture_dir, workspace = Path(args.capture_dir), Path(args.workspace)

    report = {"pdk_root": str(PDK_ROOT), "library": LIBRARY, "checks": []}
    if args.skip_capture:
        if not (capture_dir / "capture.txt").exists():
            raise SystemExit("no capture in " + str(capture_dir))
    else:
        report["capture_seconds"] = round(capture(capture_dir), 1)
    if args.capture_only:
        log("capture-only: done")
        return 0

    verify(capture_dir, workspace, report)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    failures = [item for item in report["checks"] if not item["ok"]]
    log("\n%d checks, %d failed -> %s" % (len(report["checks"]), len(failures), args.report))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
