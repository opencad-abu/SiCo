"""Validate live capture identity and publish one immutable private template."""

from pathlib import Path

from .template_build import make_template, publish, publish_bytes
from .template_capture import json_value as _json
from .template_capture import parse_capture, read_bytes
from .template_capture_diagnostics import geometry_issues
from .template_schema import SCHEMA_V3, TemplateError, arguments


def import_capture(catalog, artifact, spool, expected=None):
    if expected is not None:
        expected = arguments("extract_circuit_templates", expected)
    catalog.ensure_private_destination()
    path = Path(artifact["path"])
    if path.resolve().parent != Path(spool).resolve() or path.is_symlink():
        raise TemplateError("capture artifact is outside the session spool")
    raw = read_bytes(path)
    capture = parse_capture(raw)
    if capture["sha256"] != artifact["sha256"] or len(raw) != artifact["size"]:
        raise TemplateError("capture artifact digest/size mismatch")
    if expected:
        from .template_classification import SEMANTICS

        for name, asset in capture["assets"].items():
            header = asset["header"]
            if header.get("classification_semantics") != SEMANTICS:
                raise TemplateError(
                    "live capture requires current classification semantics; reload collector")
            if (header["lib"], header["cell"], header["view"]) != (
                expected["library"],
                expected["cell"],
                expected[name + "_view"],
            ):
                raise TemplateError("capture source does not match requested cell/view")
        reported = list(capture["assets"]) + [r["asset"] for r in capture["missing"]]
        if sorted(reported) != sorted(expected["assets"]):
            raise TemplateError("capture assets do not match requested assets")
    return store_capture(catalog, capture, raw, expected or {})


def store_capture(catalog, capture, raw, options, *, category="reference", provenance=None):
    """One publication boundary for live capture and verified saved-capture completion."""
    record = make_template(capture, category=category, provenance=provenance,
                           classifications=options.get("classifications"))
    issues = geometry_issues(capture)
    reuse = None
    if "reuse" in options:
        from .template_extract_reuse import extract_reuse

        record, reuse = extract_reuse(record, options["reuse"], issues)
    out = catalog.root / "catalogs" / (record["template_ref"] + ".sqlite3")
    raw_dir = catalog.root / "captures"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / (capture["sha256"] + ".jsonl")
    publish_bytes(raw_path, raw)
    if not out.exists():
        try:
            if record["schema_version"] == SCHEMA_V3:
                from .template_reuse_build import publish_reusable

                publish_reusable([record], out)
            else:
                publish([record], out)
        except FileExistsError:
            pass
    db = catalog._connect(out)
    try:
        stored = db.execute(
            "SELECT data_json FROM templates WHERE ref=?", (record["template_ref"],)
        ).fetchone()
        if not stored or _json(stored[0]) != record:
            raise TemplateError("immutable template publication conflict")
    finally:
        db.close()
    result = {
        "ok": True,
        "stage": "stored_private",
        **record["summary"],
        "catalog": str(out),
        "catalog_origin": {"tier": "private", "root": str(catalog.root), "catalog": str(out)},
        "capture": str(raw_path),
    }
    if issues:
        result["diagnostics"] = dict(issues=issues[:8], issue_count=len(issues))
    if reuse is not None:
        result.update(schema_version=record["schema_version"], reuse=reuse)
        if reuse["status"] != "qualified":
            result.update(ok=False, stage="stored_reference", code="template_reuse_unavailable")
            if reuse["status"] in {"needs_classification", "needs_contract"}:
                result["continuation"] = dict(tool="extract_circuit_templates", arguments=dict(
                    template_ref=record["template_ref"], reuse=options["reuse"]))
    return result
