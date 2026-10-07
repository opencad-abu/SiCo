"""Immutable SQLite template catalogs and bounded cross-session queries.

CLI: python -m cadai.template_catalog build --manifest INPUT --output OUTPUT
     python -m cadai.template_catalog import --capture INPUT --output OUTPUT
"""

from __future__ import annotations

from pathlib import Path

from .template_build import build_manifest, make_template, publish
from .template_capture import read_capture
from .template_graph import match
from .template_locations import normalize_root as normalize_root
from .template_locations import template_root as template_root
from .template_project import LEVELS, project_record
from .template_query import query_rows
from .template_schema import (
    MAX_RESPONSE_BYTES,
    SCHEMA,
    TemplateError,
    TemplateUnavailable,
    arguments,
    canonical,
)
from .template_storage import TemplateStorage


class TemplateCatalog(TemplateStorage):
    def query(self, args):
        entries = self._entries()
        all_rows = query_rows(self, args, entries)
        start, limit = args["offset"], args["limit"]
        page = bounded_page(all_rows, start, limit)
        return self.bounded(
            {
                "ok": True,
                "schema_version": SCHEMA,
                "live": False,
                "snapshot_ref": getattr(
                    getattr(self, "_last_snapshot", None), "snapshot_ref", None
                ),
                "total": len(all_rows),
                "returned": len(page),
                "offset": start,
                "next_offset": start + len(page) if start + len(page) < len(all_rows) else None,
                "templates": page,
                "catalog_sources": self.source_report(entries),
                "write_root": str(self.root),
                "match_basis": (
                    "source/text/direct_device_counts; text_aliases_are_not_function_proofs"
                ),
            }
        )

    def detail(self, args):
        record, origins = self.locate(args["template_ref"])
        section = args["section"]
        if section == "summary":
            result = {
                "ok": True,
                **record["summary"],
                **origins,
                "source": record["source"],
                "provenance": record["provenance"],
            }
            return self.bounded(result)
        if section == "preview":
            from .template_preview import render_preview

            return self.bounded(
                {
                    **render_preview(record, self.ensure_private_destination() / "previews"),
                    **origins,
                }
            )
        if section == "topology":
            data = record.get("topology")
        elif section == "reuse_contract":
            data = record.get(section)
        elif section in {"relations", "routing_style"}:
            data = record.get(section)
            if data is None:
                from .template_relations import relations, routing_style

                data = relations(record) if section == "relations" else routing_style(record)
        elif section == "parameters":
            data = record.get("parameters")
        else:
            data = record["assets"].get(section)
        if data is None:
            return self.bounded(
                {
                    "ok": False,
                    "code": "asset_unavailable",
                    "template_ref": record["template_ref"],
                    **origins,
                    "section": section,
                    "missing_assets": record["missing_assets"],
                }
            )
        entities, metadata = [], {}
        for key, value in data.items():
            if isinstance(value, list):
                if args["entity"] in {"all", key}:
                    entities.extend({"entity": key, "data": row} for row in value)
            else:
                metadata[key] = value
        if len(canonical(metadata).encode()) > 20000:
            raise TemplateUnavailable("template metadata exceeds response budget")
        page = bounded_page(entities, args["offset"], args["limit"])
        next_offset = args["offset"] + len(page)
        return self.bounded(
            {
                "ok": True,
                "template_ref": record["template_ref"],
                **origins,
                "section": section,
                "metadata": metadata,
                "total": len(entities),
                "returned": len(page),
                "records": page,
                "next_offset": next_offset if next_offset < len(entities) else None,
            }
        )

    def import_capture(self, artifact, spool, expected=None):
        from .template_capture_import import import_capture

        return import_capture(self, artifact, spool, expected)


def bounded_page(rows, offset, limit):
    page, budget = [], 0
    for row in rows[offset : offset + limit]:
        size = len(canonical(row).encode())
        if budget + size > MAX_RESPONSE_BYTES - 22000:
            if not page:
                raise TemplateUnavailable("one template record exceeds response budget")
            break
        page.append(row)
        budget += size
    return page


def call_template(name, values, workspace=None, client=None):
    try:
        import sqlite3
    except ImportError as exc:
        raise TemplateUnavailable("circuit template catalogs require Python sqlite3") from exc

    args = arguments(name, values)
    try:
        catalog = TemplateCatalog(workspace=workspace)
        if name == "query_circuit_templates":
            return catalog.query(args)
        if name == "get_circuit_template":
            return catalog.detail(args)
        if name == "match_circuit_template":
            left, origins = catalog.locate(args["template_ref"])
            right, target_origins = (
                (left, origins)
                if args["target_ref"] == args["template_ref"]
                else catalog.locate(args["target_ref"])
            )
            if "topology" not in left or "topology" not in right:
                raise TemplateUnavailable("matching requires schematic topology on both templates")
            if args["match_mode"] == "core":
                from .template_core_match import match_core

                return catalog.bounded({"ok": True, "template_ref": args["template_ref"],
                    "target_ref": args["target_ref"], **origins, "target_origins": target_origins,
                    **match_core(left, right["topology"], port_map=args["port_map"],
                                 omitted_groups=args["omitted_groups"])})
            if args["omitted_groups"]:
                raise TemplateError("omitted_groups requires match_mode=core")
            return catalog.bounded(
                {
                    "ok": True,
                    "template_ref": args["template_ref"],
                    "target_ref": args["target_ref"],
                    **origins,
                    "target_origins": target_origins,
                    **match(left["topology"], right["topology"], args["port_map"]),
                }
            )
        if "template_ref" in args:
            from .template_capture_complete import complete_capture

            if not workspace:
                raise TemplateUnavailable("saved completion requires a project workspace")
            return complete_capture(catalog, args)
        if not workspace or client is None:
            raise TemplateUnavailable(
                "live extraction requires a project workspace and Virtuoso client"
            )
        catalog.ensure_private_destination()
        ok, detail = client.call("capture_circuit_template", args)
        from .skill_diagnostics import carry_output, record_output

        record_output(detail)
        if not ok:
            return carry_output({"ok": False, **detail}, detail)
        artifact = detail.get("artifact")
        if not isinstance(artifact, dict):
            raise TemplateUnavailable("Virtuoso did not return a capture artifact")
        return carry_output(catalog.import_capture(artifact, client.runtime.spool, args), detail)
    except TemplateError:
        raise
    except (OSError, sqlite3.Error, ImportError, KeyError, TypeError, ValueError) as exc:
        raise TemplateUnavailable(str(exc)) from exc


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    imp = commands.add_parser("import")
    imp.add_argument("--capture", type=Path, action="append", required=True)
    imp.add_argument("--output", type=Path, required=True)
    project = commands.add_parser("project")
    project.add_argument("--workspace", type=Path, required=True)
    project.add_argument("--template-ref", required=True)
    project.add_argument("--level", choices=list(LEVELS), required=True)
    project.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        result = build_manifest(args.manifest, args.output)
    elif args.command == "import":
        result = publish(
            [make_template(read_capture(p)) for p in args.capture], args.output
        )
    else:
        catalog = TemplateCatalog(workspace=args.workspace)
        record = catalog.get(args.template_ref)
        projected = project_record(record, args.level)
        result = publish([projected], args.output)
        result["detail_level"] = projected["detail_level"]
        result["base_template_ref"] = record["template_ref"]
    print(canonical(result))


if __name__ == "__main__":
    main()
