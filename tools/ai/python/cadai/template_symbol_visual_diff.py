"""Build source/generated symbol review bundles and label-level visual diffs."""

from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path

from .template_catalog import TemplateCatalog
from .template_preview import render_preview
from .template_schema import TemplateError, TemplateUnavailable, canonical
from .template_symbol_labels import inventory_labels

REPORT_FORMAT = "sico.symbol.visual-diff.v1"
SCREENSHOT_FORMAT = "sico.symbol.screenshot.v1"


def _catalog(path):
    path = Path(path).resolve()
    if path.name == "catalog.sqlite3":
        root = path.parent
    elif path.parent.name == "catalogs" and path.suffix == ".sqlite3":
        root = path.parent.parent
    else:
        raise TemplateError("catalog must be catalog.sqlite3 or catalogs/*.sqlite3")
    if not path.is_file():
        raise TemplateError("visual diff catalog is missing: " + str(path))
    return TemplateCatalog(root=root)


def _records(catalog, references):
    if references:
        return [catalog.get(reference) for reference in references]
    rows = catalog.query({"offset": 0, "limit": 100, "data_format": "legacy"}).get("templates", [])
    return [catalog.get(row["template_ref"]) for row in rows]


def load_screenshots(path):
    if not path:
        return {}
    path = Path(path).resolve()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise TemplateError("cannot read screenshot manifest: " + str(exc)) from exc
    if data.get("format") != SCREENSHOT_FORMAT or not isinstance(data.get("screenshots"), list):
        raise TemplateError("unsupported screenshot manifest")
    result = {}
    for row in data["screenshots"]:
        reference = row.get("template_ref") if isinstance(row, dict) else None
        screenshot = row.get("path") if isinstance(row, dict) else None
        if not isinstance(reference, str) or not isinstance(screenshot, str):
            raise TemplateError("screenshot manifest rows require template_ref and path")
        source = Path(screenshot)
        if not source.is_absolute():
            source = path.parent / source
        if not source.is_file():
            raise TemplateError("screenshot is missing: " + str(source))
        if reference in result:
            raise TemplateError("duplicate screenshot for template: " + reference)
        result[reference] = source.resolve()
    return result


def _artifact(preview, name):
    for item in preview["artifacts"]:
        path = Path(item["path"])
        if path.name == name:
            return path
    raise TemplateUnavailable("visual preview did not produce " + name)


def _label_differences(source, plan, plan_report):
    preserved = {}
    for item in plan.get("annotations", []):
        preserved.setdefault(item.get("source_text", item["text"]), item)
    dropped = set(plan_report.get("dropped_labels", []))
    rows, unexpected = [], []
    for label in source["labels"]:
        text, kind = label["text"], label["kind"]
        if kind in {"pin_label", "fixed_annotation", "cds_term"}:
            category = "regenerated"
        elif kind in {"plain_text", "cds_param"} and text in preserved:
            category = "preserved"
        elif text in dropped:
            category = "dropped"
        elif plan_report.get("style") == "rectangular_module":
            category = "rectangular_fallback"
        else:
            category = "unexpected"
        row = {"category": category, "kind": kind, "text": text}
        if category == "unexpected":
            unexpected.append(row)
        else:
            rows.append(row)
    return rows, unexpected


def _allowed_summary(rows):
    groups = {}
    for row in rows:
        group = groups.setdefault(
            row["category"], {"category": row["category"], "count": 0, "texts": []}
        )
        group["count"] += 1
        group["texts"].append(row["text"])
    return sorted(groups.values(), key=lambda row: row["category"])


def _bundle(record, output, screenshot=None):
    bundle = output / record["template_ref"]
    bundle.mkdir(parents=True, exist_ok=True)
    preview = render_preview(record, bundle)
    source_path = _artifact(preview, "symbol.svg")
    generated_path = _artifact(preview, "symbol-proposed.svg")
    plan_path = _artifact(preview, "symbol-plan.json")
    plan = json.loads(plan_path.read_text())
    plan_report = preview["reports"]["symbol_plan"]
    asset = record.get("assets", {}).get("symbol")
    if not asset:
        raise TemplateUnavailable("visual diff requires a symbol asset")
    source = inventory_labels(asset, {p["name"] for p in asset.get("ports", [])})
    allowed, unexpected = _label_differences(source, plan, plan_report)
    screenshot_name = None
    if screenshot is not None:
        screenshot_name = "cadence-screenshot" + screenshot.suffix
        shutil.copy2(screenshot, bundle / screenshot_name)
    geometry = {
        "source_drawn_shapes": sum(
            1
            for shape in asset.get("shapes", [])
            if (shape.get("figure") or {}).get("lpp") == ["device", "drawing"]
        ),
        "source_pins": len(asset.get("pins", [])),
        "generated_body_shapes": len(plan.get("body", [])),
        "generated_stubs": len(plan.get("stubs", [])),
        "generated_rows": len(plan.get("rows", [])),
        "added_ports": plan.get("added_ports", []),
    }
    overlaps = plan_report.get("annotation_overlaps", [])
    result = {
        "library": record["source"]["lib"],
        "cell": record["source"]["cell"],
        "template_ref": record["template_ref"],
        "style": plan_report.get("style"),
        "label_policy": plan_report.get("label_policy"),
        "label_fallback": plan_report.get("label_fallback"),
        "fallback_reason": plan_report.get("fallback_reason"),
        "source_labels": source["counts"],
        "annotation_count": plan_report.get("annotation_count", 0),
        "dropped_labels": sorted(plan_report.get("dropped_labels", [])),
        "allowed_differences": _allowed_summary(allowed),
        "unexpected_labels": unexpected,
        "geometry": geometry,
        "annotation_overlaps": overlaps,
        "passed": not unexpected and not overlaps,
        "review_status": "pending_human" if screenshot_name else "pending_screenshot",
        "source_svg": str(source_path.relative_to(bundle)),
        "generated_svg": str(generated_path.relative_to(bundle)),
        "screenshot": screenshot_name,
        "preview": str(Path(preview["preview"]).relative_to(bundle)),
    }
    rows = "".join(
        f"<tr><td>{html.escape(item['category'])}</td><td>{item['count']}</td>"
        f"<td>{html.escape(', '.join(item['texts']))}</td></tr>"
        for item in result["allowed_differences"]
    )
    unexpected_rows = "".join(
        f"<li>{html.escape(item['text'])} ({item['kind']})</li>"
        for item in result["unexpected_labels"]
    )
    screenshot_html = (
        f'<h3>Cadence screenshot</h3><img src="{html.escape(screenshot_name)}">'
        if screenshot_name
        else "<p>Cadence screenshot: not supplied.</p>"
    )
    page = (
        '<!doctype html><meta charset="utf-8"><title>Symbol visual diff</title>'
        "<style>body{font:15px sans-serif;max-width:1200px;margin:24px auto;color:#18344b}"
        "img{max-width:100%;background:white;border:1px solid #d8e2eb}"
        "td,th{padding:4px 8px;border-bottom:1px solid #eee;vertical-align:top}"
        "pre{white-space:pre-wrap;background:#f6f8fa;padding:12px}</style>"
        f"<h1>{html.escape(result['library'])}/{html.escape(result['cell'])}</h1>"
        f"<p>style={html.escape(str(result['style']))}; "
        f"label_policy={html.escape(str(result['label_policy']))}; "
        f"review={html.escape(result['review_status'])}</p>"
        "<table><tr><th>source</th><th>generated</th></tr>"
        f'<tr><td><img src="{html.escape(result["source_svg"])}"></td>'
        f'<td><img src="{html.escape(result["generated_svg"])}"></td></tr></table>'
        f"{screenshot_html}<h2>Allowed differences</h2>"
        "<table><tr><th>category</th><th>count</th><th>texts</th></tr>"
        f"{rows}</table><h2>Unexpected labels</h2><ul>{unexpected_rows}</ul>"
        "<h2>Diff JSON</h2><pre>"
        + html.escape(
            canonical({k: result[k] for k in result if k not in {"source_svg", "generated_svg"}})
        )
        + "</pre>"
    )
    (bundle / "visual-diff.html").write_text(page)
    result["visual_diff"] = "visual-diff.html"
    return result


def run(catalog_path, output_path, references=None, screenshot_path=None):
    catalog = _catalog(catalog_path)
    records = _records(catalog, references or [])
    screenshots = load_screenshots(screenshot_path)
    output = Path(output_path)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise TemplateError("visual diff output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    items, errors = [], []
    for record in records:
        try:
            items.append(_bundle(record, output, screenshots.get(record["template_ref"])))
        except (TemplateError, TemplateUnavailable, OSError, ValueError) as exc:
            errors.append({"template_ref": record.get("template_ref"), "error": str(exc)})
    report = {
        "format": REPORT_FORMAT,
        "catalog": str(Path(catalog_path).resolve()),
        "output": str(output.resolve()),
        "templates": len(items),
        "errors": errors,
        "review_pending": sum(item["review_status"] == "pending_screenshot" for item in items),
        "passed": not errors and all(item["passed"] for item in items),
        "items": items,
    }
    (output / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    cards = "".join(
        f'<li><a href="{html.escape(item["template_ref"] + "/visual-diff.html")}">'
        f"{html.escape(item['library'] + '/' + item['cell'])}</a> — "
        f"{html.escape(str(item['style']))}, {item['annotation_count']} annotations, "
        f"{html.escape(item['review_status'])}</li>"
        for item in items
    )
    page = (
        '<!doctype html><meta charset="utf-8"><title>Symbol visual diff index</title>'
        "<style>body{font:15px sans-serif;max-width:1000px;margin:24px auto;color:#18344b}</style>"
        f"<h1>Symbol visual diff: {len(items)} templates</h1><ul>{cards}</ul>"
        f"<p>Errors: {len(errors)}</p><pre>{html.escape(json.dumps(errors, indent=1))}</pre>"
    )
    (output / "index.html").write_text(page)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--template-ref", action="append", default=[])
    parser.add_argument("--screenshot-manifest", type=Path)
    args = parser.parse_args(argv)
    report = run(args.catalog, args.output, args.template_ref, args.screenshot_manifest)
    print(json.dumps({k: report[k] for k in ("templates", "passed", "review_pending", "errors")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
