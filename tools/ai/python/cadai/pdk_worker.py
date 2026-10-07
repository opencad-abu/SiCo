"""One isolated, persistent dbAccess reader using a protected SKILL context."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import time
import uuid
from sicosessionenv import ENVIRONMENT_NAMES as SESSION_ENVIRONMENT_NAMES

from .env_names import value as env_value
from .pdk_data import binding
from .pdk_normalize import context, digest
from .pdk_schema import MAX_CAPTURE_BYTES, PdkUnavailable, skill_string
from . import process_monitor

WORKER_REVISION = "20260919.ai.pdk.worker.v2"


def stable_device(capture):
    """Compare disk observations without process-local DB counters or timestamps."""
    try:
        data = capture["data"]
        if any(not isinstance(data[key], dict) for key in ("identity", "cdf", "database")):
            raise ValueError()
        state = data["database"].get("master_state") or {}
        if not isinstance(state, dict):
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise PdkUnavailable("invalid_capture", "Incomplete background device capture") from exc
    if state.get("modified") is not False:
        raise PdkUnavailable("pdk_worker_context_mismatch", "PDK master must be saved before background collection")
    return digest({key: ({k: v for k, v in data[key].items() if k != "master_state"}
                         if key == "database" else data[key])
                   for key in ("identity", "cdf", "database")})


def private_directory(path):
    if not path.exists():
        private_directory(path.parent)
        path.mkdir(mode=0o700, exist_ok=True)


def private_text(path, value):
    with open(path, "w", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        stream.write(value)


def configuration(config, environment):
    """Persist identities, never environment values or proprietary context contents."""
    files = {}
    for name, path in zip(("dbAccess", "context", "initialization"), config[2:]):
        if path is None:
            files[name] = None
            continue
        info = path.stat()
        files[name] = {"path": str(path), "stat": [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]}
        if name != "dbAccess":
            files[name]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"files": files, "environment_digest": digest(worker_environment(environment)),
            "worker_revision": WORKER_REVISION}


def worker_environment(environment):
    env = dict(environment)
    for key in tuple(env):
        if key in SESSION_ENVIRONMENT_NAMES or key.startswith("CDS_MPS_") or key in {
            "CDS_LIB", "CDS_CDSLIB", "CAD_AI_SOCKET", "CAD_AI_TOKEN",
            "SICO_SOCKET", "SICO_TOKEN", "LD_PRELOAD", "LD_AUDIT",
        }:
            env.pop(key, None)
    return env


def settings(environment, origin):
    try:
        workers = int(env_value(environment, "PDK_WORKERS", "8"))
        timeout = float(env_value(environment, "PDK_WORKER_TIMEOUT", "180"))
        if not 1 <= workers <= 32 or not 1 <= timeout <= 3600:
            raise ValueError()
    except (TypeError, ValueError, OverflowError) as exc:
        raise PdkUnavailable("invalid_pdk_workers", "SICO_PDK_WORKERS must be 1–32; worker timeout must be 1–3600 seconds") from exc
    root = Path(__file__).resolve().parents[2]
    context_file = Path(env_value(environment, "PDK_WORKER_CONTEXT", str(root / "context/cadPdkWork.cxt"))).expanduser().resolve()
    if context_file.suffix != ".cxt" or not context_file.is_file():
        raise PdkUnavailable("pdk_worker_context_unavailable", "Protected PDK worker context is unavailable; configure SICO_PDK_WORKER_CONTEXT")
    install = origin.get("cadence_install_path")
    default = str(Path(install) / "bin/dbAccess") if install else "dbAccess"
    executable = shutil.which(env_value(environment, "PDK_DBACCESS", default), path=environment.get("PATH", os.defpath))
    if not executable:
        raise PdkUnavailable("pdk_worker_unavailable", "The project's dbAccess executable is unavailable")
    startup = env_value(environment, "PDK_WORKER_INIT")
    if startup:
        startup = Path(startup).expanduser().resolve()
        if not startup.is_file() or startup.suffix != ".cxt":
            raise PdkUnavailable("invalid_pdk_worker_init", "SICO_PDK_WORKER_INIT must be a site PDK initialization context")
    return workers, timeout, Path(executable), context_file, startup


def read_response(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_CAPTURE_BYTES + 1)
    if len(raw) > MAX_CAPTURE_BYTES:
        raise PdkUnavailable("capture_limit", "PDK worker response exceeds its byte budget")
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError) as exc:
        raise PdkUnavailable("invalid_capture", "Invalid PDK worker response") from exc


def stop_group(process):
    """Own only the private worker group, including helpers after the leader exits."""
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    # Always cover TERM-resistant descendants, even when the leader exited first.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=3)


class DbAccessWorker:
    def __init__(self, root, environment, origin, library, cancel, config, slot):
        self.root, self.cancel, self.slot = Path(root), cancel, slot
        self.origin, self.library = origin, library
        self.timeout = config[1]
        self.process = None
        self.process_record = None
        self.log = None
        self.root.mkdir(mode=0o700)
        try:
            lines = []
            for row in origin["libraries"]:
                name, path = row["name"], row.get("resolved_path")
                if not path:
                    continue
                if any(c.isspace() or c in '#"\\' for c in name + path):
                    raise PdkUnavailable("pdk_worker_context_mismatch", "A library mapping cannot be represented in the worker cds.lib")
                lines.append("DEFINE " + name + " " + path)
            private_text(self.root / "cds.lib", "\n".join(lines) + "\n")
            executable, context_file, startup = config[2:]
            boot = f'unless(loadContext({skill_string(str(context_file))} t) error("PDK context load failed"))\n'
            if startup:
                boot += (f'unless(loadContext({skill_string(str(startup))} t) error("PDK init load failed"))\n'
                         f"callInitProc({skill_string(startup.stem)})\n")
            boot += (f"aiPdkWorker({skill_string(str(self.root))} {skill_string(str(os.getpid()))})\nexit()\n")
            private_text(self.root / "boot.il", boot)
            env = worker_environment(environment)
            env["PWD"] = str(self.root)
            self.log = open(self.root / "worker.log", "wb", opener=lambda p, flags: os.open(p, flags, 0o600))
            self.process = subprocess.Popen(
                [str(executable), "-nocdsinit", "-cdslib", str(self.root / "cds.lib"),
                 "-load", str(self.root / "boot.il"), "-log", str(self.root / "CDS.log")],
                cwd=self.root, env=env, stdin=subprocess.DEVNULL, stdout=self.log,
                stderr=self.log, start_new_session=True, pass_fds=(slot,), umask=0o077)
            self.process_record = process_monitor.track(
                self.process, self.process.args, self.root, name="dbAccess · " + library,
                logs=(self.root / "worker.log", self.root / "CDS.log"),
            )
            ready = self._wait(self.root / "ready.json")
            if ready.get("worker_revision") != WORKER_REVISION:
                raise PdkUnavailable("pdk_worker_context_mismatch", "PDK worker context revision differs")
            self.validate(ready)
        except BaseException:
            self.close()
            raise

    def validate(self, capture):
        try:
            observed = context(capture["context"])
            expected = context(self.origin)
            if binding(observed, self.library) != binding(expected, self.library):
                raise ValueError()
            mappings = {row["name"]: row.get("resolved_path") for row in observed["libraries"]}
            if any(mappings.get(row["name"]) != row["resolved_path"]
                   for row in expected["libraries"] if row.get("resolved_path")):
                raise ValueError()
        except (KeyError, TypeError, ValueError) as exc:
            raise PdkUnavailable("pdk_worker_context_mismatch", "Background PDK bindings or tool version differ from the live session") from exc

    def _wait(self, path):
        deadline = time.monotonic() + self.timeout
        while True:
            if self.cancel.is_set():
                raise PdkUnavailable("collection_cancelled", "PDK collection cancelled")
            if path.is_file():
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    raise PdkUnavailable("invalid_capture", "Worker response is not a private regular file")
                return read_response(path)
            if self.process.poll() is not None:
                raise PdkUnavailable("pdk_worker_exited", "Background dbAccess exited; inspect the private worker log")
            if time.monotonic() >= deadline:
                raise PdkUnavailable("pdk_worker_timeout", "Background dbAccess exceeded its task timeout")
            if sum(p.stat().st_size for p in self.root.glob("*.log")) > 16 * 1024 * 1024:
                raise PdkUnavailable("pdk_worker_log_limit", "Background dbAccess exceeded its log budget")
            self.cancel.wait(0.025)

    def capture(self, target):
        request_id = uuid.uuid4().hex
        result_path = self.root / "result.json"
        result_path.unlink(missing_ok=True)
        request = "(" + " ".join(skill_string(v) for v in (
            request_id, target["library"], target["cell"], target["view"])) + ")\n"
        private_text(self.root / "request.tmp", request)
        os.replace(self.root / "request.tmp", self.root / "request")
        result = self._wait(result_path)
        if result.get("request_id") != request_id:
            raise PdkUnavailable("invalid_capture", "PDK worker response identity differs")
        if result.get("ok") is not True:
            raise PdkUnavailable("capture_unavailable", "Background device capture failed")
        self.validate(result)
        if result.get("data", {}).get("identity", {}).get("target") != target:
            raise PdkUnavailable("invalid_capture", "PDK worker returned a different device")
        stable_device(result)
        return result

    def close(self):
        try:
            if self.process is not None and self.process.poll() is None:
                # Let an idle worker exit through SKILL, avoiding Cadence's
                # SIGTERM panic dump. A blocked capture still has a hard bound.
                try:
                    private_text(self.root / "stop", "")
                    self.process.wait(timeout=0.5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            stop_group(self.process)
            process_monitor.finish(self.process_record,
                                   self.process.returncode if self.process else None)
            self.process = None
        finally:
            if self.log:
                self.log.close()
                self.log = None
