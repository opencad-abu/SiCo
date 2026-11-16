"""Read one bounded DRC rule catalog through the DataHub Calibre owners."""

from __future__ import annotations

from pathlib import Path

from sico_datahub.calibre.context import resolve_flow
from sico_datahub.calibre.runset import catalog_drc_runset
from sico_datahub.errors import HubError

NAME = "inspect_layout_drc_rules"
MAX_GROUPS = 200
MAX_LAYERS = 200


class DrcRulesError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _text(value, field, *, maximum=4096):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise DrcRulesError("invalid_arguments", f"{field} is invalid")
    return value


def _file(value, field):
    path = Path(_text(value, field)).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise DrcRulesError("invalid_arguments", f"{field} must be an absolute file")
    return path.resolve()


def _directory(value, field):
    path = Path(_text(value, field)).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise DrcRulesError("invalid_arguments", f"{field} must be an absolute directory")
    return path.resolve()


def _integer(value, field, *, default, minimum, maximum):
    if value is None:
        return default
    if type(value) is not int or not minimum <= value <= maximum:
        raise DrcRulesError(
            "invalid_arguments", f"{field} must be between {minimum} and {maximum}"
        )
    return value


def _calibre(calibre_root):
    if calibre_root is None:
        return None
    root = _directory(calibre_root, "calibre_root")
    executable = root / "bin" / "calibre"
    if not executable.is_file():
        raise DrcRulesError(
            "capability_not_available", "Calibre executable is unavailable"
        )
    return executable


def _group_rows(catalog, page):
    rows = []
    for name in page:
        members = list(catalog.members.get(name, ()))
        rows.append({
            "name": name,
            "count": catalog.counts.get(name, 0),
            "members": members[:16],
            "members_truncated": len(members) > 16,
        })
    return rows


def build_catalog(arguments, *, flow_resolver=None, catalog_reader=None):
    if not isinstance(arguments, dict):
        raise DrcRulesError("invalid_arguments", "arguments must be an object")
    allowed = {
        "config_path",
        "work_root",
        "calibre_root",
        "timeout_seconds",
        "offset",
        "limit",
    }
    unknown = set(arguments) - allowed
    if unknown:
        raise DrcRulesError("invalid_arguments", "unknown argument: " + sorted(unknown)[0])
    config_path = _file(arguments.get("config_path"), "config_path")
    work_root = _directory(arguments.get("work_root"), "work_root")
    calibre = _calibre(arguments.get("calibre_root"))
    timeout = _integer(
        arguments.get("timeout_seconds"), "timeout_seconds",
        default=60, minimum=1, maximum=300,
    )
    offset = _integer(arguments.get("offset"), "offset", default=0, minimum=0,
                      maximum=4096)
    limit = _integer(arguments.get("limit"), "limit", default=MAX_GROUPS, minimum=1,
                     maximum=MAX_GROUPS)
    try:
        flow = (flow_resolver or resolve_flow)(
            "drc", str(config_path), str(work_root), tool_version=""
        )
        reader = catalog_reader or catalog_drc_runset
        catalog = reader(
            flow.runset.path,
            calibre=calibre,
            timeout=float(timeout),
            cache_dir=(work_root / ".sico_datahub" / "calibre" / "catalog")
            if calibre else None,
            expand_tvf=bool(calibre),
            tool_version="",
        )
    except HubError as exc:
        raise DrcRulesError(exc.code, exc.message) from exc
    except (OSError, ValueError, TypeError) as exc:
        raise DrcRulesError("read_failed", "DRC rule catalog failed") from exc
    total = len(catalog.groups)
    page = catalog.groups[offset:offset + limit]
    next_offset = offset + len(page) if offset + len(page) < total else None
    return {
        "ok": True,
        "schema": "cad.drc_rules.v1",
        "top_cell": flow.top_cell,
        "config": flow.config.to_dict(),
        "runset": catalog.runset.to_dict(),
        "dependencies_sha256": catalog.dependencies_sha256,
        "source": catalog.source,
        "error": catalog.error,
        "tool_version": catalog.tool_version,
        "counts": {
            "groups": total,
            "layers": len(catalog.layers),
        },
        "groups": _group_rows(catalog, page),
        "layers": list(catalog.layers[:MAX_LAYERS]),
        "warnings": [] if calibre else ["calibre_not_configured"],
        "page": {
            "offset": offset,
            "limit": limit,
            "total": total,
            "next": next_offset,
            "truncated": next_offset is not None,
        },
    }


__all__ = ["DrcRulesError", "NAME", "build_catalog"]
