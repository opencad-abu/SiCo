"""Standalone read-only capability probe for pyaether and Custom Compiler."""

import argparse
import base64
import importlib
import importlib.util
import json
import os
import platform
import runpy
import socket
import sys
import types

MARKER = "SICO_DATAHUB_PROBE "
CONTRACT = "sico.site.capability.v1"
ACTION = "probe"
SCHEMA = "site.capability.v1"
SAFE_ENVIRONMENT = (
    "AETHER_HOME",
    "CNI_ROOT",
    "PYTHONHOME",
    "PYTHONPATH",
    "SICO_HOME",
    "SYNOPSYS_CUSTOM_PYTHON_VERSION",
)
LOAD_MODES = ("import", "spec", "runpy", "exec")


def _bounded(value, limit=1024):
    if not isinstance(value, str):
        return value
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def _load_import(path):
    directory = os.path.dirname(path)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return importlib.import_module("sico_datahub_wire")


def _load_spec(path):
    spec = importlib.util.spec_from_file_location("sico_datahub_wire_site", path)
    if spec is None or spec.loader is None:
        raise ImportError("wire spec could not be created")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_runpy(path):
    return types.SimpleNamespace(**runpy.run_path(path, run_name="sico_datahub_wire_site"))


def _load_exec(path):
    namespace = {"__name__": "sico_datahub_wire_site", "__file__": path}
    with open(path, "rb") as stream:
        source = stream.read()
    exec(compile(source, path, "exec"), namespace)
    return types.SimpleNamespace(**namespace)


def load_wire(path, mode):
    if mode == "import":
        return _load_import(path)
    if mode == "spec":
        return _load_spec(path)
    if mode == "runpy":
        return _load_runpy(path)
    if mode == "exec":
        return _load_exec(path)
    raise ValueError("Unsupported wire load mode")


def probe_load_modes(path):
    results = {}
    modules = {}
    for mode in LOAD_MODES:
        try:
            modules[mode] = load_wire(path, mode)
            results[mode] = {"ok": True, "error": ""}
        except Exception as exc:  # Probe records failures without failing the process.
            results[mode] = {
                "ok": False,
                "error": _bounded(type(exc).__name__ + ": " + str(exc)),
            }
    return results, modules


def _socket_capability():
    if not hasattr(socket, "socket"):
        return {"socket": False, "socketpair": False, "error": "socket missing"}
    pair = None
    try:
        if hasattr(socket, "socketpair"):
            pair = socket.socketpair()
        return {"socket": True, "socketpair": pair is not None, "error": ""}
    except Exception as exc:
        return {
            "socket": True,
            "socketpair": False,
            "error": _bounded(type(exc).__name__ + ": " + str(exc)),
        }
    finally:
        if pair is not None:
            for endpoint in pair:
                try:
                    endpoint.close()
                except OSError:
                    pass


def _codec_capability():
    try:
        json.loads(json.dumps({"probe": "ok"}))
        base64.b64decode(base64.b64encode(b"probe"))
        return {"json": True, "base64": True, "error": ""}
    except Exception as exc:
        return {
            "json": False,
            "base64": False,
            "error": _bounded(type(exc).__name__ + ": " + str(exc)),
        }


def _main_state():
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    return {
        "file": _bounded(getattr(main, "__file__", "") or ""),
        "package": _bounded(getattr(main, "__package__", "") or ""),
        "spec": _bounded(getattr(spec, "name", "") or "") if spec is not None else "",
    }


def _payload(scope, selected_mode, load_results, wire):
    capabilities = _socket_capability()
    codecs = _codec_capability()
    capabilities.update({
        "json": codecs["json"],
        "base64": codecs["base64"],
        "codec_error": codecs["error"],
        "read_only": True,
        "wire_load_methods": load_results,
    })
    exports = [
        name for name in (
            "HubError", "make_envelope", "validate_envelope",
            "encode_envelope", "decode_envelope",
        )
        if hasattr(wire, name)
    ]
    return {
        "schema": SCHEMA,
        "scope": scope,
        "runtime": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable": _bounded(sys.executable),
            "platform": _bounded(platform.platform()),
            "isolated": bool(getattr(sys.flags, "isolated", 0)),
        },
        "process": {
            "cwd": _bounded(os.getcwd()),
            "main": _main_state(),
            "sys_path": [_bounded(value, 512) for value in sys.path[:20]],
        },
        "environment": {
            name: _bounded(os.environ[name])
            for name in SAFE_ENVIRONMENT if name in os.environ
        },
        "capabilities": capabilities,
        "wire": {
            "hub": getattr(wire, "HUB", ""),
            "load_mode": selected_mode,
            "exports": exports,
        },
    }


def _fallback_error(request_id, scope, load_results):
    return {
        "hub": "sico.datahub.v1",
        "contract": CONTRACT,
        "version": 1,
        "id": request_id,
        "action": ACTION,
        "status": "error",
        "payload": {},
        "meta": {
            "trace_id": "",
            "source": "python-site",
            "target": "python",
            "deadline_ms": 5000,
        },
        "error": {
            "code": "capability_not_available",
            "message": "sico_datahub_wire could not be loaded",
            "details": {"scope": scope, "load_methods": load_results},
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wire", required=True)
    parser.add_argument("--load-mode", choices=LOAD_MODES, default="import")
    parser.add_argument("--scope", default="site")
    parser.add_argument("--id", default="site-probe")
    args = parser.parse_args(argv)
    wire_path = os.path.abspath(args.wire)
    load_results, modules = probe_load_modes(wire_path)
    selected = modules.get(args.load_mode)
    if selected is None:
        envelope = _fallback_error(args.id, args.scope, load_results)
    else:
        payload = _payload(args.scope, args.load_mode, load_results, selected)
        envelope = selected.make_envelope(
            CONTRACT,
            ACTION,
            args.id,
            payload,
            meta={
                "trace_id": "",
                "source": "python-site",
                "target": "python",
                "deadline_ms": 5000,
            },
        )
    print(MARKER + json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
