"""CLI and compatibility imports for Cadence text-view imports.

ImportRequest/run_import, environment and executable helpers retain their
original paths for MTS and external callers. Retire these compatibility
exports once supported callers use the owning modules. No implementation
module imports this CLI module or reads mutable facade state.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from typing import Optional, Sequence

from .errors import Nl2ViewError
from .nl2view_diagnostics import (
    append_wrapper_diagnostics,
    has_fatal_cadence_diagnostic,
    new_log_diagnostics,
)
from .nl2view_environment import import_environment
from .nl2view_executables import find_cds_lib_debug, find_executable
from .nl2view_inputs import request_from_namespace
from .nl2view_models import FORMAT_SPECS, FormatSpec, ImportRequest
from .nl2view_runner import run_import

# Private names remain as one-way aliases for integrations that inspected the
# old module during diagnostics; implementation and state live in the owning
# diagnostics module.
_has_fatal_cadence_diagnostic = has_fatal_cadence_diagnostic
_append_wrapper_diagnostics = append_wrapper_diagnostics
_new_log_diagnostics = new_log_diagnostics

__all__ = [
    "FORMAT_SPECS", "FormatSpec", "ImportRequest", "Nl2ViewError",
    "build_parser", "find_cds_lib_debug", "find_executable",
    "import_environment", "main", "request_from_namespace", "run_import",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nl2view",
        description=(
            "Create a Cadence Spectre, HSPICE, SPICE, or DSPF text view from a "
            "netlist using cdsTextTo5x."
        ),
    )
    parser.add_argument(
        "--format",
        dest="netlist_format",
        required=True,
        choices=tuple(FORMAT_SPECS),
        help="input netlist format",
    )
    parser.add_argument("--library", required=True, help="destination OA library")
    parser.add_argument(
        "--cell",
        required=True,
        help="destination cell",
    )
    parser.add_argument("--view", help="destination view name")
    parser.add_argument(
        "--cdslib",
        metavar="FILE",
        help="Cadence library definitions file (default: ./cds.lib)",
    )
    parser.add_argument("--log", metavar="FILE", help="cdsTextTo5x log file")
    source_mode = parser.add_mutually_exclusive_group()
    source_mode.add_argument(
        "--copy",
        dest="copy_source",
        action="store_true",
        help="copy the netlist into the view (default)",
    )
    source_mode.add_argument(
        "--link",
        dest="copy_source",
        action="store_false",
        help="let the view refer to the source netlist with a symbolic link",
    )
    parser.set_defaults(copy_source=True)
    parser.add_argument(
        "--cds-text-to-5x",
        default="cdsTextTo5x",
        metavar="PROGRAM",
        help="cdsTextTo5x executable (default: resolve cdsTextTo5x from PATH)",
    )
    parser.add_argument(
        "--cds-lib-debug",
        metavar="PROGRAM",
        help=(
            "cdsLibDebug executable used to resolve cds.lib with Cadence "
            "semantics (default: companion of cdsTextTo5x)"
        ),
    )
    parser.add_argument(
        "--expected-library-path",
        metavar="DIRECTORY",
        help=(
            "require --cdslib to resolve the target library to this directory "
            "(used by an active Virtuoso session)"
        ),
    )
    parser.add_argument("source", metavar="NETLIST", help="netlist text file to import")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        request = request_from_namespace(args)
        executable = find_executable(request.executable)
        if executable is None:
            print(
                f"nl2view: cannot execute {request.executable!r}: program not found",
                file=sys.stderr,
            )
            return 127
        cds_lib_debug = find_cds_lib_debug(
            executable,
            args.cds_lib_debug,
            allow_path_lookup=os.sep not in args.cds_text_to_5x,
        )
        if args.cds_lib_debug and cds_lib_debug is None:
            print(
                f"nl2view: cannot execute {args.cds_lib_debug!r}: program not found",
                file=sys.stderr,
            )
            return 127
        return run_import(
            replace(
                request,
                executable=executable,
                cds_lib_debug=cds_lib_debug,
            )
        )
    except Nl2ViewError as exc:
        parser.error(str(exc))
    except FileNotFoundError as exc:
        print(
            f"nl2view: cannot execute {args.cds_text_to_5x!r}: {exc}",
            file=sys.stderr,
        )
        return 127
    except PermissionError as exc:
        print(
            f"nl2view: cannot execute {args.cds_text_to_5x!r}: {exc}",
            file=sys.stderr,
        )
        return 126
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
