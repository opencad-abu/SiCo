"""Run declared source regressions with separate processes and private evidence."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from a_philosophy_sources import git
from engineering_gate import source_identity

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "tools/utility/source_tests.json"


def configuration(root):
    spec = json.loads((root / MANIFEST).read_text())
    if spec["schema_version"] != 1:
        raise ValueError("Unsupported source-test manifest")
    paths = json.loads((root / spec["source_paths"]).read_text())["search_paths"]
    paths = ["tools", *paths]
    for path in paths:
        if not (root / path).resolve().is_relative_to(root.resolve()):
            raise ValueError("Source search path leaves checkout: " + path)
    return spec, paths


def selection(root, spec, groups, allow_eda):
    selected = []
    for name in dict.fromkeys(groups):
        if name not in spec["groups"]:
            raise ValueError("Unknown source-test group: " + name)
        group = spec["groups"][name]
        if group["kind"] == "eda" and not allow_eda:
            raise ValueError("EDA group requires --allow-eda and a separately authorized environment")
        if group["kind"] not in {"ordinary", "gui", "runtime", "eda", "release-policy"}:
            raise ValueError("Unknown source-test kind: " + group["kind"])
        for path in group["paths"]:
            source = (root / path).resolve()
            if not source.is_relative_to(root.resolve()) or not source.is_file():
                raise ValueError("Missing or external test input: " + path)
        if not group["paths"] or not group["purpose"]:
            raise ValueError("Empty group: " + name)
        selected.append((name, group))
    if not selected:
        raise ValueError("Select at least one test group")
    return selected


def environment(root, paths, *, group, codex=None):
    # Do not inherit site model credentials, opt-in flags, pytest options or source roots.
    keep = {"HOME", "USER", "LOGNAME", "PATH", "TMPDIR", "LANG", "LC_ALL", "TZ", "SYSTEMROOT"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=os.pathsep.join(str(root / p) for p in paths),
               SICO_HOME=str(root), SICO_PYTHON=sys.executable)
    if group.get("compatibility_imports"):
        env["CAD_RUN_COMPAT_SOURCE_IMPORTS"] = "1"
    if group["kind"] == "eda":
        env["CAD_RUN_SKILL_SOURCE_PROBES"] = "1"
        for key in ("DISPLAY", "XAUTHORITY", "CDS_LIC_FILE", "LM_LICENSE_FILE"):
            if key in os.environ:
                env[key] = os.environ[key]
    if group["kind"] == "runtime":
        if codex is None or not codex.is_absolute() or not codex.is_file():
            raise ValueError("Runtime group requires --codex /absolute/path/to/original-binary")
        env["CAD_TEST_COPILOT_CODEX_CLI"] = str(codex)
    return env


def outcomes(path):
    cases = {}
    for case in ET.parse(path).iter("testcase"):
        key = case.attrib.get("classname", "") + "::" + case.attrib["name"]
        if key in cases:
            raise ValueError("Duplicate testcase: " + key)
        result = next((v for v in case if v.tag in {"failure", "error", "skipped"}), None)
        cases[key] = dict(status=result.tag if result is not None else "passed",
                          reason=result.attrib.get("message", "") if result is not None else "")
    counts = {state: sum(v["status"] == state for v in cases.values())
              for state in ("passed", "failure", "error", "skipped")}
    return dict(counts=counts, cases=cases)


def run(root, groups, output, *, allow_eda=False, codex=None):
    root, output = root.resolve(), output.resolve()
    if output.is_relative_to(root) and output.relative_to(root).parts[:1] != (".cad",):
        raise ValueError("In-checkout test evidence must stay under .cad")
    spec, paths = configuration(root)
    selected = selection(root, spec, groups, allow_eda)
    prepared = [(name, group, environment(root, paths, group=group, codex=codex))
                for name, group in selected]
    output.mkdir(parents=True, exist_ok=True)
    report_dir = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    prefixes = {p.stem if p.is_file() else p.name for base in paths if base != "tools"
                for p in (root / base).iterdir() if p.is_dir() or p.suffix == ".py"}
    prefixes -= {"tests", "__pycache__", "__init__"}
    head = git(root, "rev-parse", "HEAD").decode().strip()
    before = source_identity(root)
    report = dict(schema_version=1, root=str(root), commit=head, inputs_sha256=before,
                  interpreter=sys.executable, python=sys.version, groups={}, errors=[],
                  not_covered=spec["not_covered"], output=str(report_dir))
    report.update(status="running", passed=False, formal_runtime_qualified=False)
    report["dependencies"] = {}
    for name in spec.get("dependencies", []):
        try:
            report["dependencies"][name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            report["dependencies"][name] = None
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    if any(g["kind"] == "runtime" for _, g in selected):
        report["codex"] = dict(path=str(codex), sha256=hashlib.sha256(codex.read_bytes()).hexdigest())
    for name, group, env in prepared:
        xml, imports = report_dir / (name + ".xml"), report_dir / (name + "-imports.json")
        env.update(SICO_SOURCE_TEST_ROOT=str(root), SICO_SOURCE_TEST_REPORT=str(imports),
                   SICO_SOURCE_TEST_PREFIXES=json.dumps(sorted(prefixes)))
        command = [sys.executable, "-B", "-s", "-m", "pytest", "-q", "-ra",
                   "-p", "no:cacheprovider", "-p", "utility.source_test_report",
                   "--rootdir=" + str(root), *group["paths"], "--junitxml=" + str(xml)]
        print(f"{name}: {group['purpose']}", flush=True)
        with (report_dir / (name + ".log")).open("w") as stream:
            result = subprocess.run(command, cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT)
        row = dict(kind=group["kind"], purpose=group["purpose"], command=command,
                   returncode=result.returncode, paths=group["paths"], errors=[])
        try:
            row.update(outcomes(xml))
            loaded = json.loads(imports.read_text())
            row["imports"] = dict(path=str(imports), count=len(loaded["modules"]))
            row["errors"].extend(loaded["errors"])
            if not row["counts"]["passed"]:
                row["errors"].append("No passed test cases; skips/collection are not acceptance")
        except (OSError, ValueError, ET.ParseError) as exc:
            row["errors"].append(str(exc))
        row["passed"] = (result.returncode == 0 and not row["errors"] and
                         not any(row["counts"][s] for s in ("failure", "error", "skipped")))
        row["status"] = ("failed" if result.returncode or row["errors"] else
                         "passed" if row["passed"] else "passed_with_skips")
        report["groups"][name] = row
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"{name}: {row['status']} {row.get('counts', {})}", flush=True)
    if source_identity(root) != before or git(root, "rev-parse", "HEAD").decode().strip() != head:
        report["errors"].append("Source/configuration changed during tests")
    if "codex" in report and hashlib.sha256(codex.read_bytes()).hexdigest() != report["codex"]["sha256"]:
        report["errors"].append("Selected Codex binary changed during tests")
    report["passed"] = not report["errors"] and all(r["passed"] for r in report["groups"].values())
    failed = bool(report["errors"]) or any(r["status"] == "failed" for r in report["groups"].values())
    report["status"] = "failed" if failed else "passed" if report["passed"] else "passed_with_skips"
    report["formal_runtime_qualified"] = False
    (report_dir / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", action="append", help="Repeat to select declared groups")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / ".cad/source-tests")
    parser.add_argument("--codex", type=Path, help="Original binary for the explicit local runtime group")
    parser.add_argument("--allow-eda", action="store_true")
    args = parser.parse_args(argv)
    try:
        spec, _ = configuration(ROOT)
        if args.list:
            print(json.dumps(spec, ensure_ascii=False, indent=2))
            return 0
        report = run(ROOT, args.group or spec["default_groups"], args.output,
                     allow_eda=args.allow_eda, codex=args.codex)
    except KeyboardInterrupt:
        print("Interrupted; summary remains incomplete (passed=false).", file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        args.output.mkdir(parents=True, exist_ok=True)
        report = dict(status="error", passed=False, errors=[str(exc)])
        (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        print(str(exc), file=sys.stderr)
        return 2
    print("Evidence: " + report["output"])
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
