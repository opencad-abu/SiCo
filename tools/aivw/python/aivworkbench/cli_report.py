"""Qualification report publication at the command boundary."""

from __future__ import annotations

import argparse
from pathlib import Path
from .errors import AivwError


def write_report(report: object, args: argparse.Namespace) -> Path:
    from .agent.qualification import write_qualification_report

    try:
        return write_qualification_report(report, args.report, overwrite=args.overwrite)
    except ValueError as exc:
        raise AivwError("cannot write qualification report: %s" % exc) from exc
