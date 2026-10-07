"""Run a passive stdio provider with only controller-staged public files.

The worker has private user/PID/network/mount namespaces, no host home or
project mounts, no inherited credentials or descriptors, and a read-only
public bundle. Model-service access must go through an independently qualified
controller broker; this worker deliberately has no network capability.
"""

from __future__ import annotations

import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from ..workspace import sha256_file


def inspect_bundle(root):
    root = Path(root)
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)) or not root.is_dir():
        raise ValueError("public worker bundle must be an absolute regular directory")
    records, size = [], 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("public worker bundle contains a nonregular entry")
        if path.is_file():
            size += path.stat().st_size
            if size > 64*1024*1024 or len(records) >= 256:
                raise ValueError("public worker bundle exceeds limit")
            records.append({"path": str(path.relative_to(root)), "sha256": sha256_file(path)})
    return records


def run_public_worker(bundle, request, *, timeout=30., max_output_bytes=2*1024*1024):
    """Execute one public ``worker.py``; return bounded bytes and provenance.

    No fallback to unisolated execution exists. The controller must stage only
    public context/code; this function cannot determine a file's classification.
    """
    bundle = Path(bundle)
    records = inspect_bundle(bundle)
    if not (bundle/"worker.py").is_file() or not isinstance(request, bytes) or len(request) > 2*1024*1024:
        raise ValueError("bounded worker.py and byte request required")
    if not 0 < timeout <= 600 or not 0 < max_output_bytes <= 4*1024*1024:
        raise ValueError("worker budget outside bounds")
    runtime = Path("/software/pkgs/python/3.9.13").resolve(strict=True)
    bwrap = Path("/usr/bin/bwrap")
    if not bwrap.is_file():
        raise ValueError("namespace isolation unavailable")
    command = [str(bwrap), "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL",
               "--clearenv", "--setenv", "PATH", "/runtime/bin:/usr/bin", "--setenv", "HOME", "/scratch",
               "--setenv", "LD_LIBRARY_PATH", "/runtime/lib", "--setenv", "PYTHONNOUSERSITE", "1",
               "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
               "--ro-bind", str(runtime), "/runtime", "--ro-bind", str(bundle), "/public",
               "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/scratch",
               "--chdir", "/scratch", "/runtime/bin/python3", "-I", "/public/worker.py"]
    started = time.monotonic()
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={"PATH": "/usr/bin:/bin", "LANG": "C"}, close_fds=True, start_new_session=True)
    selector = selectors.DefaultSelector()
    output, errors = bytearray(), bytearray()
    pending = memoryview(request)
    try:
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            os.set_blocking(pipe.fileno(), False)
        selector.register(proc.stdout, selectors.EVENT_READ, output)
        selector.register(proc.stderr, selectors.EVENT_READ, errors)
        if pending:
            selector.register(proc.stdin, selectors.EVENT_WRITE, None)
        else:
            proc.stdin.close()
        while selector.get_map():
            remaining = timeout-(time.monotonic()-started)
            if remaining <= 0:
                raise TimeoutError("isolated provider deadline exhausted")
            for key, _ in selector.select(min(.1, remaining)):
                if key.fileobj is proc.stdin:
                    try:
                        count = os.write(proc.stdin.fileno(), pending[:65536])
                        pending = pending[count:]
                    except BrokenPipeError:
                        pending = b""
                    if not pending:
                        selector.unregister(proc.stdin)
                        proc.stdin.close()
                else:
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    else:
                        key.data.extend(data)
                        if len(output)+len(errors) > max_output_bytes:
                            raise ValueError("isolated provider output exceeds limit")
        returncode = proc.wait(timeout=max(.01, timeout-(time.monotonic()-started)))
        if returncode != 0:
            raise ValueError("isolated provider failed with exit %d" % returncode)
        if inspect_bundle(bundle) != records:
            raise ValueError("public worker bundle changed during execution")
        return bytes(output), {"status": "EXECUTED", "isolation": "linux_namespace_public_stdio_v1",
            "network": "isolated", "host_project_mounted": False, "public_bundle": records,
            "runtime_sha256": sha256_file(runtime/"bin/python3"), "bwrap_sha256": sha256_file(bwrap),
            "exit_code": returncode, "elapsed_seconds": time.monotonic()-started}
    finally:
        selector.close()
        # Include descendants that outlive the direct process.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if not pipe.closed:
                pipe.close()
