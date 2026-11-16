"""Explicit one-process DataHub bridge for site and Calibre capabilities."""

import argparse
import json
import os
import signal
import stat
import sys
import threading
from pathlib import Path

from sico_datahub.endpoint import UnixEnvelopeServer
from sico_datahub.envelope import Envelope, Meta
from sico_datahub.errors import HubError
from sico_datahub.calibre.handlers import calibre_handlers
from sico_datahub.calibre.runner import CalibreRunner
from sico_datahub.site import probe_capabilities

BRIDGE_MARKER = "SICO_DATAHUB_BRIDGE "
BRIDGE_SCHEMA = "datahub.bridge.v1"


def _private_file(path, mode):
    path = Path(path)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or stat.S_IMODE(info.st_mode) != mode
    ):
        raise ValueError("Bridge file must be a private regular file")
    return path


def read_token(path):
    path = _private_file(path, 0o600)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        value = os.read(fd, 1025)
    finally:
        os.close(fd)
    if not 0 < len(value) <= 1024:
        raise ValueError("Bridge token file is empty or oversized")
    try:
        token = value.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("Bridge token must be ASCII") from exc
    if not token or any(char.isspace() for char in token):
        raise ValueError("Bridge token is invalid")
    return token


def write_descriptor(path, value):
    path = Path(path)
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(
        str(path),
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def site_handler(site_python, *, isolate):
    def handle(request):
        payload = probe_capabilities(
            site_python,
            scope=request.payload["scope"],
            isolate=isolate,
        )
        return Envelope(
            contract=request.contract,
            version=request.version,
            action=request.action,
            id=request.id,
            payload=payload,
            meta=Meta(source="bridge", target="python-site"),
        )

    return handle


def _tool_executable(root, candidates):
    for relative in candidates:
        candidate = root / relative
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return root / candidates[0]


def tool_probe_handler(contract, schema, root, executables):
    root = Path(root).expanduser().absolute()

    def handle(request):
        if request.payload["tool_root"] != str(root):
            raise HubError("invalid_arguments", "Tool root does not match this bridge")
        executable = _tool_executable(root, executables)
        return Envelope(
            contract=request.contract,
            version=request.version,
            action=request.action,
            id=request.id,
            payload={
                "schema": schema,
                "tool_root": str(root),
                "executable": str(executable),
                "available": executable.is_file() and os.access(executable, os.X_OK),
                "read_only": True,
            },
            meta=Meta(source="bridge", target="python"),
        )

    return handle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--session-ref", required=True)
    parser.add_argument("--site-python")
    parser.add_argument("--calibre-root")
    parser.add_argument("--calibre-work-root")
    parser.add_argument("--calibre-timeout", type=float, default=24 * 60 * 60)
    parser.add_argument("--max-calibre-jobs", type=int, default=4)
    parser.add_argument("--drc-command")
    parser.add_argument("--lvs-command")
    parser.add_argument("--xcelium-root")
    parser.add_argument("--descriptor")
    parser.add_argument("--no-isolate", action="store_true")
    args = parser.parse_args(argv)

    token = read_token(args.token_file)
    handlers = {}
    calibre_runner = None
    descriptor = {
        "schema": BRIDGE_SCHEMA,
        "session_ref": args.session_ref,
        "pid": os.getpid(),
    }
    if args.site_python:
        site_python = str(Path(args.site_python).expanduser().absolute())
        handlers[("sico.site.capability.v1", "probe")] = site_handler(
            site_python, isolate=not args.no_isolate
        )
        descriptor.update(
            site_python=site_python,
            isolate=not args.no_isolate,
        )
    if args.calibre_root:
        root = str(Path(args.calibre_root).expanduser().absolute())
        descriptor["calibre_root"] = root
        if args.calibre_work_root:
            tools_root = Path(__file__).resolve().parents[2]
            drc_command = args.drc_command or str(tools_root / "drc" / "bin" / "drc")
            lvs_command = args.lvs_command or str(tools_root / "lvs" / "bin" / "lvs")
            calibre_runner = CalibreRunner(
                args.calibre_work_root,
                calibre_root=root,
                drc_command=drc_command,
                lvs_command=lvs_command,
                timeout=args.calibre_timeout,
                max_active=args.max_calibre_jobs,
            )
            handlers.update(calibre_handlers(calibre_runner))
            descriptor.update(
                calibre_work_root=str(calibre_runner.work_root),
                drc_command=str(calibre_runner.commands["drc"]),
                lvs_command=str(calibre_runner.commands["lvs"]),
                calibre_timeout=args.calibre_timeout,
                calibre_jobs=True,
            )
        else:
            for contract, schema in (
                ("sico.calibre.lvs.v1", "calibre.lvs.probe.v1"),
                ("sico.calibre.drc.v1", "calibre.drc.probe.v1"),
            ):
                handlers[(contract, "probe")] = tool_probe_handler(
                    contract, schema, root, ("bin/calibre",)
                )
            descriptor["calibre_jobs"] = False
    elif args.calibre_work_root:
        parser.error("--calibre-work-root requires --calibre-root")
    if args.xcelium_root:
        root = str(Path(args.xcelium_root).expanduser().absolute())
        handlers[("sico.xcelium.run.v1", "probe")] = tool_probe_handler(
            "sico.xcelium.run.v1",
            "xcelium.run.probe.v1",
            root,
            ("tools/bin/xrun", "tools.lnx86/bin/xrun", "bin/xrun"),
        )
        descriptor["xcelium_root"] = root
    if not handlers:
        parser.error("At least one bridge handler must be configured")
    server = UnixEnvelopeServer(
        args.socket,
        token,
        args.session_ref,
        handlers,
    )
    descriptor_path = Path(args.descriptor) if args.descriptor else None
    stopped = threading.Event()
    previous = {}
    try:
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous[number] = signal.signal(number, lambda *_args: stopped.set())
        server.start()
        descriptor["endpoint"] = str(server.path)
        if descriptor_path is not None:
            write_descriptor(descriptor_path, descriptor)
        print(
            BRIDGE_MARKER
            + json.dumps(descriptor, ensure_ascii=False, sort_keys=True),
            flush=True,
        )
        while not stopped.wait(0.25):
            pass
        return 0
    finally:
        if calibre_runner is not None:
            calibre_runner.close()
        server.close()
        if descriptor_path is not None:
            descriptor_path.unlink(missing_ok=True)
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, HubError) as exc:
        print("sico-datahub-bridge: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
