"""Development-only batch template collector over isolated read-only sessions.

This source-tree CLI and its private mapping/evidence stay outside runtime
packages; scope is declared in ``deploy/python_native_build.json``.
Every capture goes through the production MCP tool ``extract_circuit_templates``
inside a private Xvfb + Virtuoso session whose ``cds.lib`` only adds a
read-only ``INCLUDE`` of the source library list.  Source views are opened
read-only, never saved or repaired, and no editor window is created.  One
session is restarted per chunk so a crashed capture cannot silently lose the
remaining cells; a JSONL mapping file resumes an interrupted batch.

Run as::

    python -m cadai.template_collect --queue queue.jsonl --batch NAME \
        --mapping mapping.jsonl --session DIR [--classifications file.jsonl] \
        [--chunk 8] [--attempts 2] [--failures-report report.json]
"""

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

from .headless_session import AI_ROOT, session
from .template_schema import canonical, TemplateError

CAPTURE_SCHEMA = "cad.template.capture.v2"
ASSETS = ("schematic", "symbol", "layout")
# aiTplCapture dependencies; AI_protocol.il is loaded by the session bootstrap.
MODULES = (
    "AI_templates.il",
    "AI_template_capture_geometry.il",
    "AI_template_native_content.il",
    "AI_circuit_create_check.il",
)

FAILURE_CLASSES = (
    ("source_missing", ("Template master unavailable", "no readable source views",
                        "does not exist")),
    ("budget", ("geometry inventory budget", "artifact size", "record budget",
                "exceeds 8 MiB", "exceeds 2 MiB")),
    ("classification", ("classification refers", "cell_property", "classifications")),
    ("timeout", ("timeout", "Timeout")),
)


def classify_failure(error_text):
    """Coarse, printable class of one failure for batch reports."""
    text = str(error_text)
    for name, needles in FAILURE_CLASSES:
        if any(needle in text for needle in needles):
            return name
    return "other"


def classification_map(path):
    """Per-target explicit device classifications supplied to the public entry."""
    if not path:
        return {}
    result = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            # Inventories are recorded per reference; several view variants of
            # one cell carry their own instance names.
            key = row.get("legacy_ref") or row.get("template_ref") or (
                row["library"] + "/" + row["cell"])
            result[key] = row["classifications"]
    return result


def target_key(row):
    """Resume identity: the recorded reference, else the explicit target."""
    return row.get("legacy_ref") or (row["library"] + "/" + row["cell"])


def batch_targets(queue, batch):
    """Every distinct target of one batch; a cell can carry several recorded views."""
    targets = {}
    for line in Path(queue).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if batch and row.get("batch") != batch:
            continue
        targets.setdefault(target_key(row), row)
    return list(targets.values())


def result_map(path):
    if not path.exists():
        return {}
    return {
        target_key(row): row
        for row in (json.loads(line) for line in path.read_text().splitlines() if line.strip())
    }


def source_list(row):
    """The audited wrapper pins historical resolution; the source cds.lib is the fallback."""
    return row.get("retained_wrapper") or row["source_cds_lib"]


def attach(bridge, directory, source_cds_lib):
    """Point the isolated session at the audited source library list, read-only."""
    with (directory / "cds.lib").open("a") as stream:
        stream.write("INCLUDE " + str(source_cds_lib) + "\n")
    bridge.skill("ddUpdateLibList()")
    for name in MODULES:
        bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")
    bridge.skill(
        "unless(isCallable('aiTplCapture) error(\"template capture entry is unavailable\")) "
        "unless(isCallable('aiTplDependencies) "
        "error(\"capture v2 geometry module is unavailable\"))"
    )
    # This is a dedicated background session with no editors, so accepted saved
    # views stay accepted across captures; the dependency and native byte guards
    # still prove the saved content of every single capture.
    bridge.skill("aiTplIsolatedSession=t")
    original = bridge.request

    def request(message, timeout):
        if message["method"] == "capture_circuit_template":
            bridge.evaluate_path(
                message["source_path"], message["output_path"], evaluator="aiEvalExpression",
                timeout=timeout,
            )
            return True, {"result_path": message["output_path"]}
        return original(message, timeout)

    bridge.request = request


def extract(server, row, classifications=None):
    """Exactly the public entry the assistant uses; the result carries the new reference."""
    from .headless_session import mcp_call

    arguments = dict(
        library=row["library"],
        cell=row["cell"],
        assets=list(ASSETS),
        capture_schema=CAPTURE_SCHEMA,
    )
    # The retained inventory pins the historical view of every asset; a cell can
    # carry several recorded variants (for example schematic_estimated), and each
    # reference keeps the views its own capture used.
    for asset, info in (row.get("assets") or {}).items():
        view = (info or {}).get("view")
        if asset in ASSETS and view and view != asset:
            arguments[asset + "_view"] = view
    if classifications:
        arguments["classifications"] = classifications
    return mcp_call(server, "extract_circuit_templates", arguments)


def capture_group(directory, source_cds_lib, group, classifications=None):
    """Capture every cell of one group; a dead session returns the unfinished remainder."""
    captured, remainder = [], list(group)
    with session(directory) as (bridge, server, _):
        attach(bridge, directory, source_cds_lib)
        server.client.runtime = server.client.dispatcher.runtime
        while remainder:
            row = remainder[0]
            try:
                supplied = (classifications or {}).get(target_key(row))
                detail = extract(server, row, supplied)
                if not isinstance(detail, dict) or not detail.get("template_ref"):
                    raise RuntimeError(
                        "extraction returned no template reference: "
                        + str({k: detail.get(k) for k in ("code", "message")
                               if isinstance(detail, dict)})[:400]
                    )
            except BaseException as exc:  # session or capture failure: retry in a new session
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                data = getattr(exc, "data", None) or {}
                output = str(data.get("output", ""))[-600:]
                return captured, remainder, f"{type(exc).__name__}: {exc} {output}"
            captured.append((row, detail))
            remainder.pop(0)
    return captured, [], None


def run(args):
    directory = args.session.resolve()
    if directory.exists() and args.reset:
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    mapping_path = args.mapping.resolve()
    previous = result_map(mapping_path)
    targets = batch_targets(args.queue, args.batch)
    if args.limit:
        targets = targets[: args.limit]
    sources = {source_list(row) for row in targets}
    if len(sources) != 1:
        raise TemplateError("one batch must share exactly one source list: " + str(sources))
    pending = [row for row in targets if target_key(row) not in previous]
    stream = mapping_path.open("a")
    failures = {}
    attempts = {}
    attempt = len(list(directory.glob("run-*")))
    while pending:
        group, pending = pending[: args.chunk], pending[args.chunk :]
        for row in group:
            print("capture", row["library"], row["cell"], flush=True)
        attempt += 1
        captured, remainder, error = capture_group(
            directory / f"run-{attempt}", source_list(row), group,
            classification_map(args.classifications),
        )
        for source, detail in captured:
            entry = dict(
                legacy_ref=source.get("legacy_ref"),
                template_ref=detail["template_ref"],
                library=source["library"],
                cell=source["cell"],
                catalog=source.get("catalog"),
                dataset=source.get("dataset"),
                batch=source.get("batch"),
                capture_sha256=detail["capture_sha256"],
                package=detail["package"],
                capabilities=detail["capabilities"],
                session=str(directory),
            )
            stream.write(canonical(entry) + "\n")
            stream.flush()
            previous[target_key(source)] = entry
        if remainder:
            stuck = remainder[0]
            key = target_key(stuck)
            attempts[key] = attempts.get(key, 0) + 1
            print("session stopped:", key, error, "attempt", attempts[key], file=sys.stderr)
            if attempts[key] >= args.attempts:
                failures[stuck["library"] + "/" + stuck["cell"] + "#" + key[-8:]] = error
                remainder = remainder[1:]
            pending = remainder + pending
    stream.close()
    summary = dict(
        batch=args.batch,
        requested=len(targets),
        captured=len(previous),
        failed=len(failures),
        failures=failures,
    )
    if args.failures_report:
        classes = Counter(classify_failure(text) for text in failures.values())
        report = dict(summary, failure_classes=dict(classes))
        Path(args.failures_report).write_text(
            json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(canonical(summary))
    return 0 if not failures else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--chunk", type=int, default=8)
    parser.add_argument("--attempts", type=int, default=2, help="retries per cell")
    parser.add_argument(
        "--classifications", type=Path,
        help="jsonl with per-target explicit device classifications",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--failures-report", type=Path,
                        help="write the summary plus failure classes as JSON")
    parser.add_argument("--reset", action="store_true", help="delete an existing session dir")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
