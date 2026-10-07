"""Bounded read-only access to a site-configured document/runtime device catalog."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3

from sicostate import project_root

from .circuit_tools import checked_arguments
from .env_names import DEVICE_CATALOG_ENV, DEVICE_CATALOG_LEGACY, resolved_name
from .optional_resources import resource_path


class DeviceCatalogUnavailable(RuntimeError):
    pass


def normalize_catalog_path(value, *, workspace=None):
    return resource_path(value, DEVICE_CATALOG_ENV, workspace=workspace)


def catalog_path(workspace=None, environment=None):
    env = os.environ if environment is None else environment
    variable = resolved_name(env, DEVICE_CATALOG_ENV, DEVICE_CATALOG_LEGACY)
    configured = str(env.get(variable, "")).strip()
    if configured:
        return normalize_catalog_path(configured, workspace=workspace)
    candidates = [Path(__file__).resolve().parents[2] / "reference/virtuoso-base-libraries/catalog.sqlite3"]
    if workspace:
        candidates.insert(0, project_root(workspace) / "ai/reference/virtuoso-base-libraries/catalog.sqlite3")
    return next((p for p in candidates if p.is_file()), candidates[-1])


def _summary(record):
    runtime = record.get("runtime") or {}
    symbol = runtime.get("symbol") or {}
    return {
        "library": record["library"], "cell": record["cell"],
        "doc_status": record["doc_status"], "runtime_status": record["runtime_status"],
        "categories": record["categories"], "description": record["description"][:1200],
        "guidance": record["guidance"], "common_parameters": record["common_parameters"],
        "views": runtime.get("views", []), "symbol": symbol,
        "base_cdf_count": len(runtime.get("base_cdf", [])), "sources": record["sources"],
    }


def query_catalog(arguments, workspace=None, database=None):
    args = checked_arguments("query_device_catalog", arguments)
    try:
        path = normalize_catalog_path(database, workspace=workspace) if database else catalog_path(workspace)
        if not path.is_file():
            raise DeviceCatalogUnavailable(
                "Device catalog is not configured. Use search_pdk_devices for prepared PDK "
                "devices, or set " + DEVICE_CATALOG_ENV + " to a site-managed catalog"
            )
        if path.stat().st_size > 64 * 1024 * 1024:
            raise DeviceCatalogUnavailable("Device catalog exceeds 64 MiB reference limit")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            db.set_progress_handler(lambda: 1, 10000000)
            metadata = {key: json.loads(value) for key, value in db.execute(
                "SELECT key, value_json FROM metadata WHERE key IN ('schema_version','runtime','qualification')")}
            if metadata.get("schema_version") != 1:
                raise DeviceCatalogUnavailable("Unsupported device catalog schema")
            clauses, values = [], []
            for key in ("library", "cell"):
                if key in args:
                    clauses.append(key + " = ? COLLATE BINARY")
                    values.append(args[key])
            if "query" in args:
                clauses.append("instr(lower(search_text), lower(?)) > 0")
                values.append(args["query"])
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            total = db.execute("SELECT count(*) FROM cells" + where, values).fetchone()[0]
            raw = db.execute("SELECT data_json FROM cells" + where + " ORDER BY library,cell LIMIT ? OFFSET ?",
                             values + [args["limit"], args["offset"]]).fetchall()
            cells, budget = [], 0
            for row in raw:
                record = json.loads(row[0])
                item = _summary(record)
                if args.get("include_parameters") or args.get("parameter"):
                    params = (record.get("runtime") or {}).get("base_cdf", [])
                    docs = []
                    for section_row in db.execute(
                        "SELECT s.data_json FROM sections s JOIN cell_sections c ON s.id=c.section_id "
                        "WHERE c.library=? AND c.cell=? ORDER BY s.id", (record["library"], record["cell"])):
                        section = json.loads(section_row[0])
                        docs.extend({**p, "source": section["source"]} for p in section["parameters"])
                    if args.get("parameter"):
                        params = [p for p in params if p["name"] == args["parameter"]]
                        docs = [p for p in docs if p["name"] == args["parameter"]]
                    start, limit = args["parameter_offset"], args["parameter_limit"]
                    item["parameters"] = {
                        "base_cdf": params[start:start+limit], "documented": docs[start:start+limit],
                        "base_cdf_total": len(params), "documented_total": len(docs), "offset": start,
                        "truncated": start+limit < max(len(params), len(docs)),
                    }
                size = len(json.dumps(item, ensure_ascii=False).encode())
                if budget + size > 90000:
                    if not cells:
                        raise DeviceCatalogUnavailable("Single result too large; narrow parameter range")
                    break
                budget += size
                cells.append(item)
        return {"ok": True, "schema_version": 1, "catalog": str(path), "catalog_sha256": checksum,
                "qualification": "reference_only_not_simulation_qualified", "live": False,
                "runtime_provenance": metadata.get("runtime"), "total": total,
                "offset": args["offset"], "returned": len(cells),
                "truncated": args["offset"] + len(cells) < total,
                "next_offset": args["offset"] + len(cells) if args["offset"] + len(cells) < total else None,
                "cells": cells}
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        raise DeviceCatalogUnavailable(str(exc)) from exc
