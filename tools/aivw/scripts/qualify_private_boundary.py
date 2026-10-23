#!/usr/bin/env python3
"""Qualify the local private-execution boundary against one reservation.

Public evidence only: the reservation file is opened inside the boundary;
the summary records capability checks and public commitment digests.
"""

import argparse
import json
from pathlib import Path
import sys

from aivworkbench.manifest import publish_split_manifests, verify_split_manifests
from aivworkbench.qualification.private_boundary import QUALIFIED_STATUS, qualify
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reservation", type=Path, required=True,
                        help="reservation control manifest (public commitment)")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    control = args.reservation.resolve()
    details = json.loads(control.read_text())["details"]
    payload_root = Path(details["payload_root"])
    # payload_root = <lib>.<cell>.<view>/runs/<run_id>
    parts = payload_root.parents[1].name.split(".")
    library, cell = parts[0], parts[1] if len(parts) > 1 else parts[0]
    report = qualify(control, payload_root=payload_root, timeout=args.timeout)
    request = {"kind": report["kind"], "boundary": report["boundary"],
               "reservation": report["reservation"],
               "implementation": {str(Path(__file__).resolve()): sha256_file(Path(__file__))}}
    digest = stable_digest(request)
    run = allocate_split_run(resolve_launch_paths(), Path("/path/to/xh/amsVerify/data/ams").resolve(),
        library=library, cell=cell, view="dynamic_evaluator",
        kind="private-boundary-qualification", request_digest=digest)
    write_json_once(run.control_root/"request.json", request)
    write_json_once(run.payload_root/"summary.json", report)
    publish_split_manifests(run, request_digest=digest, status=report["status"],
        control_details={"payload_root": str(run.payload_root), **report["reservation"]},
        payload_details={"scope": report["scope"]},
        control_artifacts=[run.control_root/"request.json"],
        payload_artifacts=[run.payload_root/"summary.json"])
    pair = verify_split_manifests(run.control_manifest, run.payload_manifest)
    print(json.dumps({"run_id": run.run_id, "status": report["status"], "integrity": pair["status"],
                      "summary": str(run.payload_root/"summary.json")}))
    return 0 if report["status"] == QUALIFIED_STATUS else 1


if __name__ == "__main__":
    sys.exit(main())
