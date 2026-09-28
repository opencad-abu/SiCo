"""Validate command-line values and construct text-view import requests."""

from __future__ import annotations

import argparse
from pathlib import Path

from .errors import Nl2ViewError
from .nl2view_models import FORMAT_SPECS, ImportRequest


def _file_path(value: str, label: str, *, require_content: bool = False) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise Nl2ViewError(f"cannot access {label}: {path}")
    if require_content and path.stat().st_size == 0:
        raise Nl2ViewError(f"{label} is empty: {path}")
    return path


def _output_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.parent.is_dir():
        raise Nl2ViewError(f"log directory does not exist: {path.parent}")
    return path


def _directory_path(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise Nl2ViewError(f"cannot access {label}: {path}")
    return path


def _oa_name(value: str, label: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or value.startswith("-")
        or any(character.isspace() or ord(character) < 32 for character in value)
        or "/" in value
        or "\\" in value
    ):
        raise Nl2ViewError(f"invalid {label}: {value!r}")
    return value


def request_from_namespace(args: argparse.Namespace) -> ImportRequest:
    source = _file_path(args.source, "netlist file", require_content=True)
    cds_library_file = _file_path(
        args.cdslib if args.cdslib is not None else "cds.lib",
        "cds.lib",
    )
    spec = FORMAT_SPECS[args.netlist_format]
    view = args.view if args.view is not None else spec.default_view
    return ImportRequest(
        source=source,
        netlist_format=args.netlist_format,
        library=_oa_name(args.library, "library name"),
        cell=_oa_name(args.cell, "cell name"),
        view=_oa_name(view, "view name"),
        cds_library_file=cds_library_file,
        log_file=_output_path(args.log) if args.log else None,
        copy_source=args.copy_source,
        executable=args.cds_text_to_5x,
        expected_library_path=(
            _directory_path(args.expected_library_path, "expected library path")
            if args.expected_library_path
            else None
        ),
    )
