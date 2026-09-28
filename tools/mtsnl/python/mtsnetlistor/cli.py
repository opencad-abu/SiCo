"""The ``mts-netlistor`` command line interface."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from .catalog import load_source_catalog
from .environment import detach_mps_environment, read_session
from .errors import MtsNetlistorError, RequestValidationError
from .ocean import raw_path_from_output, render_ocean_script
from .publish import publish_bundle, publish_text_view
from .scoper import scope_netlist
from .workflow import generate


def _json_print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mts-netlistor")
    sub = parser.add_subparsers(dest="command", required=True)

    catalog = sub.add_parser("catalog", help="enumerate source library/cell/view metadata")
    catalog.add_argument("--source-cdslib", required=True, type=Path)
    catalog.add_argument("--dbaccess", default="dbAccess")
    catalog.add_argument("--fallback", action="store_true", help="allow non-authoritative filesystem preview")
    catalog.add_argument("--json", action="store_true")

    validate = sub.add_parser("validate", help="validate a request TOML")
    validate.add_argument("request", type=Path)
    validate.add_argument("--json", action="store_true")

    scope = sub.add_parser("scope", help="scope one raw netlist into an MTS block")
    scope.add_argument("--dialect", choices=("spectre", "hspiceD"), required=True)
    scope.add_argument("--top", required=True)
    scope.add_argument("raw", type=Path)
    scope.add_argument("--output", type=Path)
    scope.add_argument("--report", type=Path)
    scope.add_argument("--json", action="store_true")

    generate_parser = sub.add_parser("generate", help="run isolated source OCEAN and MTS scope")
    generate_parser.add_argument("request", type=Path)
    generate_parser.add_argument("--virtuoso", default=None)
    generate_parser.add_argument("--ocean", default=None)
    generate_parser.add_argument("--timeout", type=float, default=600.0)
    generate_parser.add_argument("--json", action="store_true")

    inspect = sub.add_parser("inspect", help="inspect a completed run directory")
    inspect.add_argument("run_dir", type=Path)
    inspect.add_argument("--json", action="store_true")

    publish = sub.add_parser("publish", help="publish a generated MTS netlist as a target text view")
    publish.add_argument("request", type=Path)
    publish.add_argument("--session", required=True, type=Path)
    publish.add_argument("--netlist", required=True, type=Path)
    publish.add_argument("--run-dir", required=True, type=Path)
    publish.add_argument("--cds-text-to-5x", default="cdsTextTo5x")
    publish.add_argument("--cds-lib-debug", default=None)
    publish.add_argument("--dbaccess", default="dbAccess")
    publish.add_argument("--timeout", type=float, default=120.0)
    publish.add_argument("--json", action="store_true")

    gui = sub.add_parser("gui", help="launch the optional PyQt5 workbench")
    gui.add_argument("--session", type=Path, default=None)
    gui.add_argument("--parent-pid", type=int, default=None)
    gui.add_argument("--module-root", type=Path, default=None)
    return parser


def _catalog(args: argparse.Namespace) -> int:
    result = load_source_catalog(
        args.source_cdslib,
        dbaccess=args.dbaccess,
        allow_fallback=args.fallback,
    )
    payload = result.catalog.to_dict()
    payload["authoritative"] = result.authoritative
    payload["provider"] = result.provider
    if result.diagnostics:
        payload["diagnostics"] = list(result.diagnostics)
    _json_print(payload)
    return 0


def _validate(args: argparse.Namespace) -> int:
    from .config import canonical_request_digest, load_request, request_to_dict

    request = load_request(args.request)
    payload = request_to_dict(request)
    payload["request_digest"] = canonical_request_digest(request)
    _json_print(payload)
    return 0


def _scope(args: argparse.Namespace) -> int:
    raw = args.raw.expanduser().resolve()
    if not raw.is_file() or raw.stat().st_size == 0:
        raise RequestValidationError(f"cannot access raw netlist: {raw}")
    result = scope_netlist(raw.read_text(encoding="utf-8", errors="replace"), args.dialect, args.top)
    if args.output:
        target = args.output.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.output, encoding="utf-8")
    if args.report:
        target = args.report.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result.report(), ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _json_print(result.report())
    return 0


def _generate(args: argparse.Namespace) -> int:
    from .config import load_request
    from .workflow import generate_many

    request = load_request(args.request)
    result = (
        generate_many(request, virtuoso=args.virtuoso, ocean=args.ocean, timeout=args.timeout)
        if request.cell_specs
        else generate(request, virtuoso=args.virtuoso, ocean=args.ocean, timeout=args.timeout)
    )
    _json_print(result.to_dict())
    return 0


def _inspect(args: argparse.Namespace) -> int:
    run = args.run_dir.expanduser().resolve()
    manifest = run / "manifest.json"
    if not manifest.is_file():
        raise RequestValidationError(f"run manifest is missing: {manifest}")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    _json_print(payload)
    return 0


def _publish(args: argparse.Namespace) -> int:
    from .config import load_request

    request = load_request(args.request)
    session = read_session(args.session)
    result = publish_bundle(
        request,
        session,
        netlist=args.netlist,
        run_dir=args.run_dir,
        cds_text_to_5x=args.cds_text_to_5x,
        cds_lib_debug=args.cds_lib_debug,
        dbaccess=args.dbaccess,
        timeout=args.timeout,
        strict_ownership=True,
    )
    _json_print(result.to_dict())
    # A publication can fail after the immutable preflight (for example when
    # the symbol transfer succeeds but nl2view fails).  Keep the structured
    # result on stdout, but make shell automation observe the failure.
    return 0 if result.status == "succeeded" else 1


def _gui(args: argparse.Namespace) -> int:
    # Keep Qt imports behind this command so headless CLI commands remain
    # usable on hosts that do not install PyQt5.
    from .gui.app import run_gui
    session = read_session(args.session) if args.session else None
    parent_identity = None
    if args.parent_pid is not None:
        from cadgui.lifecycle import capture_parent_identity
        parent_identity = capture_parent_identity(args.parent_pid)
    return run_gui(
        session=session,
        parent_identity=parent_identity,
        session_path=args.session,
        module_root=args.module_root,
    )


def main(argv: list[str] | None = None) -> int:
    # The GUI can be launched by Virtuoso's IPC server, which normally
    # propagates the host CDS_MPS_* identity.  Detach before parsing or
    # dispatching so even an older cached SKILL launcher cannot make this
    # process, or a child Cadence worker, join the Proj Virtuoso MPS session.
    detach_mps_environment(os.environ)
    args = build_parser().parse_args(argv)
    try:
        if args.command == "catalog":
            return _catalog(args)
        if args.command == "validate":
            return _validate(args)
        if args.command == "scope":
            return _scope(args)
        if args.command == "generate":
            return _generate(args)
        if args.command == "inspect":
            return _inspect(args)
        if args.command == "publish":
            return _publish(args)
        if args.command == "gui":
            return _gui(args)
        raise AssertionError(args.command)
    except KeyboardInterrupt:
        return 130
    except (MtsNetlistorError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"mts-netlistor: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
