"""Read-only project observations with explicit filesystem and qualification scope."""

import hashlib
import os
import socket
from pathlib import Path

from .circuit_spec_schema import digest
from .project_contract import observation
from .project_journal import now


def check_path(path, kind, minimum_bytes=0, minimum_inodes=0):
    p = Path(path)
    row = {"path": path, "resolved_path": str(p.resolve()), "kind": kind, "status": "fail"}
    try:
        st = p.stat()
        if kind == "directory":
            if not p.is_dir():
                raise OSError("not a directory")
            fs = os.statvfs(p)
            row.update(
                free_bytes=fs.f_bavail * fs.f_frsize,
                free_inodes=fs.f_favail,
                writable=os.access(p, os.W_OK | os.X_OK),
                read_only_mount=bool(fs.f_flag & os.ST_RDONLY),
            )
            if (
                not row["writable"]
                or row["read_only_mount"]
                or row["free_bytes"] < minimum_bytes
                or (minimum_inodes and (fs.f_files == 0 or fs.f_favail < minimum_inodes))
            ):
                raise OSError("directory permission, free space or inode requirement not met")
        else:
            if not p.is_file() or not os.access(p, os.R_OK):
                raise OSError("not a readable regular file")
            row.update(size_bytes=st.st_size, mtime_ns=st.st_mtime_ns)
            if kind == "executable":
                if not os.access(p, os.X_OK):
                    raise OSError("file is not executable")
            else:
                # Match listed model bytes only; recursive includes are explicitly unverified.
                if st.st_size > 256 * 1024 * 1024:
                    raise OSError("model exceeds 256 MiB preflight hashing budget")
                h = hashlib.sha256()
                total = 0
                with p.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        total += len(block)
                        if total > 256 * 1024 * 1024:
                            raise OSError("model grew beyond preflight hashing budget")
                        h.update(block)
                after = p.stat()
                if (st.st_ino, st.st_size, st.st_mtime_ns) != (
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise OSError("file changed during preflight")
                row["sha256"] = h.hexdigest()
        row["status"] = "pass"
    except OSError as exc:
        row["reason"] = str(exc)
    return row


def preflight(args, identity, observe):
    observation(observe)
    checks = []

    def add(name, passed, actual):
        checks.append({"check": name, "status": "pass" if passed else "fail", "actual": actual})

    add(
        "presentation",
        args["presentation"] == "background" or observe["gui_available"],
        {"requested": args["presentation"], "gui_available": observe["gui_available"]},
    )
    add("workflow_apis", not observe["missing_apis"], observe["missing_apis"] or [])
    add(
        "target_coverage",
        len(observe["targets"] or []) == len(args["targets"]),
        {"expected": len(args["targets"]), "observed": len(observe["targets"] or [])},
    )
    libraries = dict(observe["libraries"] or [])
    for expected in args["libraries"]:
        actual = libraries.get(expected["library"])
        add(
            "library_resolution",
            bool(actual) and Path(actual).resolve() == Path(expected["expected_path"]).resolve(),
            {**expected, "actual_path": actual},
        )
    for expected, row in zip(args["targets"], observe["targets"]):
        target, exists, view_type, readable, parent_writable, opened, modified = row
        add(
            "target_identity",
            target == [expected[k] for k in ("library", "cell", "view")],
            {"expected": {k: expected[k] for k in ("library", "cell", "view")}, "observed": target},
        )
        good = (
            not exists and not opened and parent_writable
            if expected["intent"] == "create"
            else (exists or opened) and parent_writable and view_type == expected["view_type"] == "schematic"
            if expected["intent"] == "edit"
            else exists and readable and view_type == expected["view_type"] and not modified
        )
        add(
            "target_" + expected["intent"],
            good,
            {
                "target": target,
                "exists": exists,
                "actual_view_type": view_type,
                "readable": readable,
                "parent_writable": parent_writable,
                "open_in_session": opened,
                "modified_in_session": modified,
            },
        )
    paths = [
        {
            **check_path(d["path"], "directory", d["min_free_bytes"], d["min_free_inodes"]),
            "purpose": d["purpose"],
        }
        for d in args["directories"]
    ]
    paths.extend(check_path(p, "model") for p in args["model_files"])
    if "simulator_executable" in args:
        paths.append(check_path(args["simulator_executable"], "executable"))
    passed = all(r["status"] == "pass" for r in checks + paths)
    return {
        "ok": True,
        "schema": "cad.circuit.project.preflight.v1",
        "checked_at": now(),
        "requirements_digest": digest(args),
        "session": identity,
        "checks": checks,
        "filesystem_host": socket.gethostname(),
        "paths": paths,
        "checks_passed": passed,
        "write_qualified": False,
        "simulation_qualified": False,
        "not_checked": [
            "cross_host_filesystem_identity",
            "remote_workers_and_queue_policy",
            "license_checkout",
            "simulator_version_and_execution",
            "lock_service_and_future_locks",
            "model_sections_and_recursive_includes",
            "PDK_CDF_PCell_and_callback_readiness",
            "complete_source_integrity_and_future_user_edits",
        ],
        "next_action": "execute_with_final_native_checks" if passed else "resolve_failed_checks",
    }
