"""Qualify a local private-execution boundary for the dynamic evaluator.

The boundary is a bubblewrap filesystem namespace: the evaluation process
can read the reserved private root and write a spool directory, and
nothing else is visible -- no user home, no public tree.  Host network is
retained because the EDA tools check out licenses from the site license
servers; the probe records license reachability explicitly.
The public process never reads private payloads; only the probe inside the
boundary opens the reservation file.

This is a local prototype qualification, not a production evaluator
certificate.  Site review is still required before private access.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile

from ..workspace import sha256_file


BOUNDARY_KIND = "bwrap_filesystem_namespace_v2"
QUALIFIED_STATUS = "QUALIFIED_LOCAL_NAMESPACE_V2"
DEFAULT_PYTHON = Path("/software/pkgs/python/3.9.13/bin/python3")
DEFAULT_RO_PATHS = ("/software", "/usr", "/bin", "/lib", "/lib64", "/etc")
_LICENSE_ENVIRONMENT = ("CDS_LIC_FILE", "CLIOLMD_LICENSE_FILE", "LM_LICENSE_FILE", "SNPSLMD_LICENSE_FILE")


class BoundaryError(RuntimeError):
    """The boundary could not be established; callers must fail closed."""


def license_hosts_from_environment(environment):
    """Extract ``server:port`` entries from the site license variables."""

    hosts = []
    for name in _LICENSE_ENVIRONMENT:
        for entry in str(environment.get(name, "")).split(":"):
            entry = entry.strip()
            if "@" in entry:
                port, _, server = entry.rpartition("@")
                if port.isdigit() and server:
                    hosts.append("%s:%s" % (server, port))
    return sorted(set(hosts))


_PROBE = r'''
import hashlib, json, os, socket, sys

private = os.environ["BOUNDARY_PRIVATE"]
spool = os.environ["BOUNDARY_SPOOL"]
canary = os.environ["BOUNDARY_CANARY"]
visibility = os.environ["BOUNDARY_VISIBILITY"]
license_hosts = [item for item in os.environ.get("BOUNDARY_LICENSE_HOSTS", "").split(",") if item]
expected = os.environ["BOUNDARY_EXPECTED_SHA256"]
checks = []

def check(name, passed, detail=""):
    checks.append({"name": name, "status": "PASS" if passed else "FAIL", "detail": detail})

raw = open(os.path.join(private, "reservation.json"), "rb").read()
sha = hashlib.sha256(raw).hexdigest()
record = json.loads(raw.decode("utf-8"))
check("reservation_readable", True, sha)
check("reservation_hash_matches_commitment", sha == expected, sha)

try:
    open(canary, "rb").read()
    check("outside_file_not_visible", False, "canary was readable")
except OSError:
    check("outside_file_not_visible", True, "canary is out of scope")

unreachable = []
for item in license_hosts:
    host, _, port = item.rpartition(":")
    try:
        socket.create_connection((host, int(port)), timeout=2.0).close()
    except OSError:
        unreachable.append(item)
check("license_endpoints_reachable", bool(license_hosts) and not unreachable,
      ",".join(unreachable) or ",".join(license_hosts) or "none configured")

try:
    os.makedirs(spool, exist_ok=True)
    with open(os.path.join(spool, "boundary-spool.json"), "w", encoding="utf-8") as stream:
        json.dump({"probe": "spool"}, stream)
    check("spool_writable", True, spool)
except OSError as exc:
    check("spool_writable", False, type(exc).__name__)

try:
    with open(canary + ".write", "w", encoding="utf-8") as stream:
        stream.write("forbidden")
    check("outside_write_forbidden", False, "canary path was writable")
except OSError:
    check("outside_write_forbidden", True, "outside path is read-only")

check("public_tree_not_visible", not os.path.exists(visibility), visibility)
print(json.dumps({"checks": checks, "python": sys.version.split()[0],
                  "reservation": {"sha256": sha, "case_count": len(record.get("cases", [])),
                                  "policy_digest": record.get("policy_digest"),
                                  "domain_digest": record.get("domain_digest")}}))
'''


def boundary_command(entry, *, private_root=None, spool, environment, ro_paths=DEFAULT_RO_PATHS, extra_mounts=()):
    """Build the bubblewrap command line for one boundary invocation."""

    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise BoundaryError("bwrap is unavailable; refusing to run without a boundary")
    spool = Path(spool)
    if not spool.is_absolute() or spool.is_symlink():
        raise BoundaryError("spool must be an absolute non-symlink path")
    spool.mkdir(parents=True, exist_ok=True)
    command = [bwrap, "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
               "--unshare-cgroup", "--die-with-parent", "--new-session", "--clearenv"]
    for path in ro_paths:
        if Path(path).exists():
            command += ["--ro-bind", path, path]
    # Cadence's non-graphical ocean wrapper still starts Xvfb, which needs a
    # writable /dev/shm and the X11 socket directory.
    command += ["--tmpfs", "/tmp", "--tmpfs", "/tmp/.X11-unix", "--proc", "/proc", "--dev", "/dev",
                "--tmpfs", "/dev/shm"]
    if private_root is not None:
        private = Path(private_root)
        if not private.is_absolute() or private.is_symlink() or not private.is_dir():
            raise BoundaryError("private root must be an absolute non-symlink directory")
        command += ["--ro-bind", str(private), str(private)]
    for source, destination, writable in extra_mounts:
        source, destination = Path(source), str(destination)
        if not source.is_absolute() or source.is_symlink():
            raise BoundaryError("extra mount source must be an absolute non-symlink path")
        command += ["--bind" if writable else "--ro-bind", str(source), destination]
    command += ["--bind", str(spool), str(spool), "--chdir", str(spool)]
    for name in sorted(environment):
        command += ["--setenv", name, str(environment[name])]
    command += list(entry)
    return command


def run_boundary(entry, *, private_root=None, spool, environment, timeout=120.0, ro_paths=DEFAULT_RO_PATHS, extra_mounts=()):
    command = boundary_command(entry, private_root=private_root, spool=spool, environment=environment,
                               ro_paths=ro_paths, extra_mounts=extra_mounts)
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        raise BoundaryError("boundary invocation failed: %s" % completed.stderr.strip()[:1600])
    return completed


def _parse_probe(stdout):
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    raise BoundaryError("boundary probe produced no report")


def probe(private_root, *, spool, canary, visibility, expected_sha256, license_hosts=(),
          python=DEFAULT_PYTHON, timeout=120.0):
    """Run the capability probe inside the boundary and return its report."""

    environment = {
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "BOUNDARY_PRIVATE": str(private_root),
        "BOUNDARY_SPOOL": str(spool),
        "BOUNDARY_CANARY": str(canary),
        "BOUNDARY_VISIBILITY": str(visibility),
        "BOUNDARY_LICENSE_HOSTS": ",".join(license_hosts),
        "BOUNDARY_EXPECTED_SHA256": expected_sha256,
    }
    completed = run_boundary([str(python), "-s", "-c", _PROBE], private_root=private_root,
                             spool=spool, environment=environment, timeout=timeout)
    return _parse_probe(completed.stdout)


def qualify(reservation_control, *, payload_root, python=DEFAULT_PYTHON, timeout=120.0, license_hosts=None):
    """Qualify the boundary against one public reservation commitment.

    The public process reads only the control manifest and the public
    commitment; the reservation file is opened inside the boundary.
    """

    from ..manifest import resolve_indexed_artifact
    control = Path(reservation_control)
    summary = json.loads(resolve_indexed_artifact(control, "public-commitment.json").path.read_text())
    commitment = summary["commitment"]
    private_root = Path(payload_root)/"private"
    catdir = Path(tempfile.mkdtemp(prefix="aivw-boundary-canary-", dir=str(Path(os.path.expanduser("~")))))
    canary = catdir/"canary.txt"
    canary.write_text(secrets.token_hex(16) + "\n")
    # The spool lives in a separate tree: binding it must not create the
    # canary's parent directory inside the boundary.
    spool = Path(tempfile.mkdtemp(prefix="aivw-boundary-spool-"))
    report = {"schema_version": 1, "kind": "private-execution-boundary-qualification",
              "status": "NOT_QUALIFIED", "boundary": BOUNDARY_KIND,
              "scope": "local user/mount/net namespace prototype; production evaluator still requires site review",
              "reservation": {"run_id": summary.get("run_id") or control.parent.name,
                              "commitment_sha256": commitment["commitment_sha256"],
                              "policy_digest": commitment["policy_digest"],
                              "domain_digest": commitment["domain_digest"],
                              "case_count": commitment["case_count"]},
              "private_access_authorized": False, "holdout_evaluated": False}
    try:
        result = probe(private_root, spool=spool, canary=canary,
                       visibility=str(Path(__file__).resolve().parents[4]),
                       expected_sha256=commitment["commitment_sha256"],
                       license_hosts=(license_hosts_from_environment(os.environ)
                                      if license_hosts is None else license_hosts),
                       python=python, timeout=timeout)
        report["checks"] = result["checks"]
        report["python"] = result.get("python")
        report["bwrap_sha256"] = sha256_file(Path(shutil.which("bwrap")))
        observed = result.get("reservation", {})
        fail = any(item["status"] != "PASS" for item in result["checks"])
        if (not fail and observed.get("sha256") == commitment["commitment_sha256"] and
                observed.get("case_count") == commitment["case_count"] and
                observed.get("policy_digest") == commitment["policy_digest"] and
                observed.get("domain_digest") == commitment["domain_digest"]):
            report["status"] = QUALIFIED_STATUS
        else:
            report["mismatch"] = {key: value for key, value in observed.items()
                                  if value != commitment.get(key if key != "sha256" else "commitment_sha256")}
    finally:
        shutil.rmtree(catdir, ignore_errors=True)
        shutil.rmtree(spool, ignore_errors=True)
    return report


def verify_report_record(report, commitment, *, run_status=None):
    """Pure validation of one qualification report against a commitment."""

    if (report.get("kind") != "private-execution-boundary-qualification" or
            report.get("status") != QUALIFIED_STATUS or report.get("boundary") != BOUNDARY_KIND):
        raise BoundaryError("boundary report is not a qualification")
    if run_status is not None and run_status != QUALIFIED_STATUS:
        raise BoundaryError("boundary report run status is not qualified")
    reservation = report.get("reservation", {})
    if (reservation.get("commitment_sha256") != commitment["commitment_sha256"] or
            reservation.get("policy_digest") != commitment["policy_digest"] or
            reservation.get("domain_digest") != commitment["domain_digest"] or
            reservation.get("case_count") != commitment["case_count"]):
        raise BoundaryError("boundary report belongs to another reservation")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks or any(item.get("status") != "PASS" for item in checks):
        raise BoundaryError("boundary report contains failed or missing checks")
    return True


def verify_boundary_report(control, reservation_control):
    """Verify a published boundary qualification against its reservation.

    The hidden evaluator must fail closed when this raises.
    """

    from .dynamic_admission import _json, _run
    reference = _run(control)
    report = _json(control, "summary.json")
    commitment = _json(reservation_control, "public-commitment.json")["commitment"]
    verify_report_record(report, commitment, run_status=reference["run_status"])
    if _run(control) != reference:
        raise BoundaryError("boundary report changed during verification")
    return report
