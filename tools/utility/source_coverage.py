"""Run declared offline groups under coverage and retain a private summary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from source_tests import configuration, environment, selection


ROOT = Path(__file__).resolve().parents[2]
RC_FILE = ROOT / "tools/utility/source_coverage.ini"


def totals(files):
    result = {"statements": 0, "covered_lines": 0, "branches": 0,
              "covered_branches": 0}
    for row in files.values():
        summary = row["summary"]
        result["statements"] += summary["num_statements"]
        result["covered_lines"] += summary["covered_lines"]
        result["branches"] += summary["num_branches"]
        result["covered_branches"] += summary["covered_branches"]
    result["line_pct"] = (
        100.0 * result["covered_lines"] / result["statements"]
        if result["statements"] else 100.0
    )
    result["branch_pct"] = (
        100.0 * result["covered_branches"] / result["branches"]
        if result["branches"] else 100.0
    )
    return result


def run(root, groups, output, *, minimum=0.0):
    root, output = root.resolve(), output.resolve()
    if output.is_relative_to(root) and output.relative_to(root).parts[:1] != (".cad",):
        raise ValueError("In-checkout coverage evidence must stay under .cad")
    spec, paths = configuration(root)
    selected = selection(root, spec, groups, allow_eda=False)
    output.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    data_file = run_dir / ".coverage"
    errors, group_rows = [], {}
    for name, group in selected:
        if group["kind"] not in {"ordinary", "gui"}:
            raise ValueError("Coverage groups must be ordinary or gui")
        env = environment(root, paths, group=group)
        env.pop("PYTHONNOUSERSITE", None)
        env["COVERAGE_FILE"] = str(data_file)
        command = [
            sys.executable, "-B", "-m", "coverage", "run",
            "--rcfile=" + str(RC_FILE), "--parallel-mode",
            "-m", "pytest", "-q", "-ra", "-p", "no:cacheprovider",
            "--rootdir=" + str(root), *group["paths"],
        ]
        log = run_dir / (name + ".log")
        with log.open("w") as stream:
            result = subprocess.run(command, cwd=root, env=env,
                                    stdout=stream, stderr=subprocess.STDOUT)
        group_rows[name] = dict(returncode=result.returncode, log=str(log),
                                paths=group["paths"])
        if result.returncode:
            errors.append(f"{name}: coverage pytest failed")
    if data_file.exists() or list(run_dir.glob(".coverage.*")):
        subprocess.run(
            [sys.executable, "-m", "coverage", "combine",
             "--rcfile=" + str(RC_FILE), "--data-file=" + str(data_file),
             "--keep", *[str(path) for path in run_dir.glob(".coverage.*")]],
            cwd=root, check=True,
        )
        json_path = run_dir / "coverage.json"
        subprocess.run(
            [sys.executable, "-m", "coverage", "json",
             "--rcfile=" + str(RC_FILE), "--data-file=" + str(data_file),
             "-o", str(json_path)],
            cwd=root, check=True,
        )
        report = json.loads(json_path.read_text())
    else:
        report = {"files": {}, "totals": totals({})}
        errors.append("No coverage data was collected")
    summary = totals(report.get("files", {}))
    summary.update(groups=group_rows, errors=errors,
                   status="failed" if errors or summary["line_pct"] < minimum else "passed",
                   minimum=minimum, source_commit=subprocess.check_output(
                       ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
                   ).strip())
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", action="append")
    parser.add_argument("--output", type=Path,
                        default=ROOT / ".cad/source-coverage")
    parser.add_argument("--minimum", type=float, default=0.0,
                        help="minimum overall line percentage")
    args = parser.parse_args(argv)
    try:
        spec, _ = configuration(ROOT)
        summary = run(ROOT, args.group or spec["default_groups"], args.output,
                      minimum=args.minimum)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        args.output.mkdir(parents=True, exist_ok=True)
        summary = {"status": "error", "passed": False, "errors": [str(exc)]}
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
