"""Bounded index searches and cross-tier duplicate verification for template readers."""

from __future__ import annotations

from .template_locations import TIER_ORDER
from .template_schema import TemplateUnavailable, canonical
from .template_storage import catalog_summary

MAX_SUMMARY_BYTES = 32 * 1024 * 1024
MAX_DUPLICATE_BYTES = 32 * 1024 * 1024
MAX_REFERENCE_COPIES = 10000


def _filters(args):
    clauses, values = [], []
    for key, column in (
        ("topology_fingerprint", "fingerprint"),
        ("library", "library"),
        ("cell", "cell"),
        ("category", "category"),
    ):
        if key in args:
            clauses.append(column + "=? COLLATE BINARY")
            values.append(args[key])
    for key, op in (("min_devices", ">="), ("max_devices", "<=")):
        if key in args:
            clauses.append("device_count" + op + "?")
            values.append(args[key])
    if "query" in args:
        for word in args["query"].lower().split():
            clauses.append("instr(search_text,?)>0")
            values.append(word)
    return " WHERE " + " AND ".join(clauses) if clauses else "", values


def _selected(summary, args):
    return (
        (not args.get("rule_version") or summary.get("rule_version") == args["rule_version"])
        and (
            not args.get("detail_level")
            or summary.get("detail_level") == args["detail_level"]
        )
        and (not args.get("required_asset") or args["required_asset"] in summary["assets"])
        and (
            not args.get("device_kind")
            or args["device_kind"] in summary["counts"].get("device_kinds", {})
        )
    )


def _copies(catalog, entries, references):
    """Resolve selected references across ALL tiers, even copies excluded by filters."""
    copies = {ref: [] for ref in references}
    if not references:
        return copies
    scanned = 0
    for path, locations in entries:
        with catalog._connection(path) as db:
            for offset in range(0, len(references), 500):
                batch = references[offset : offset + 500]
                sql = "SELECT ref FROM templates WHERE ref IN (" + ",".join("?" * len(batch)) + ")"
                for (ref,) in db.execute(sql, batch):
                    scanned += 1
                    if scanned > MAX_REFERENCE_COPIES:
                        raise TemplateUnavailable(
                            "reference copy lookup budget exceeded; narrow query"
                        )
                    copies[ref].append((path, locations))
    return copies


def query_rows(catalog, args, entries):
    where, values = _filters(args)
    found, scanned, scanned_bytes = {}, 0, 0
    # SQL checks bytes before materializing a potentially oversized summary in Python.
    size = "length(CAST(summary_json AS BLOB))"
    select = (
        "SELECT ref,library,cell,category,device_count,fingerprint,"
        + size
        + ",CASE WHEN "
        + size
        + "<=? THEN summary_json END FROM templates"
    )
    for path, locations in entries:
        with catalog._connection(path) as db:
            if args.get("tier") and not any(loc.tier == args["tier"] for loc in locations):
                continue
            for ref, lib, cell, category, count, fingerprint, nbytes, raw in db.execute(
                select + where, [MAX_SUMMARY_BYTES, *values]
            ):
                scanned += 1
                if nbytes is None:
                    raise TemplateUnavailable("catalog summary is null")
                scanned_bytes += nbytes
                if scanned > 10000 or scanned_bytes > MAX_SUMMARY_BYTES:
                    raise TemplateUnavailable("query scan budget exceeded; narrow the filters")
                summary = catalog_summary(raw, ref)
                if (
                    (summary["library"], summary["cell"], summary["category"])
                    != (lib, cell, category)
                    or summary["counts"].get("devices", 0) != count
                    or summary.get("topology_fingerprint") != fingerprint
                ):
                    raise TemplateUnavailable("catalog summary differs from search index")
                if _selected(summary, args):
                    if ref in found and summary != found[ref]:
                        raise TemplateUnavailable("immutable template reference conflict: " + ref)
                    found[ref] = summary

    copies = _copies(catalog, entries, sorted(found))
    compared_bytes, rows = 0, []
    for ref, summary in found.items():
        first, origins, summary_differs = None, [], False
        if not copies[ref]:
            raise TemplateUnavailable("catalog changed during query; retry")
        for path, locations in copies[ref]:
            origins.extend({**loc.describe(), "catalog": str(path)} for loc in locations)
            if len(copies[ref]) > 1:
                current = catalog._read(path, ref, max_bytes=MAX_DUPLICATE_BYTES - compared_bytes)
                if current is None:
                    raise TemplateUnavailable("catalog changed during query; retry")
                content = canonical(current)
                compared_bytes += len(content.encode())
                if compared_bytes > MAX_DUPLICATE_BYTES:
                    raise TemplateUnavailable(
                        "duplicate verification budget exceeded; narrow query"
                    )
                if first is not None and content != first:
                    raise TemplateUnavailable("immutable template reference conflict: " + ref)
                first = content
                summary_differs |= current["summary"] != summary
        if summary_differs:
            raise TemplateUnavailable("catalog summary differs from stored record: " + ref)
        origins = catalog.order_origins(origins)
        eligible = [o for o in origins if not args.get("tier") or o["tier"] == args["tier"]]
        if not eligible:
            raise TemplateUnavailable("catalog changed during query; retry")
        rows.append({**summary, "catalog_origin": eligible[0], "available_from": origins})
    catalog._verify_snapshot()
    return sorted(
        rows,
        key=lambda r: (
            TIER_ORDER[r["catalog_origin"]["tier"]],
            r["library"],
            r["cell"],
            r["template_ref"],
        ),
    )
