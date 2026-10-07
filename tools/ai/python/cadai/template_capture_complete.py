"""Complete a pinned reference with classification evidence and reuse intent."""

from .template_build import make_template
from .template_capture import parse_capture, read_bytes
from .template_capture_import import store_capture
from .template_classification import EXPLICIT_SEMANTICS
from .template_schema import SCHEMA, SCHEMA_V3, TemplateError
from .template_source_qualification import HEX64


def complete_capture(catalog, args):
    record = catalog.get(args["template_ref"])
    if record["schema_version"] not in {SCHEMA, SCHEMA_V3}:
        raise TemplateError("saved completion requires an explicit-evidence v2/v3 template")
    if record["source"].get("classification_semantics") not in EXPLICIT_SEMANTICS:
        raise TemplateError("recapture with explicit classification semantics before completion")
    catalog.ensure_private_destination()
    # Callers select a fixed reference, never a path or an alternative source identity.
    capture_sha = record.get("capture_sha256")
    if not isinstance(capture_sha, str) or not HEX64.fullmatch(capture_sha):
        raise TemplateError("saved reference has invalid capture digest")
    path = catalog.root / "captures" / (capture_sha + ".jsonl")
    capture_bytes = read_bytes(path)
    capture = parse_capture(capture_bytes)
    if capture["sha256"] != record["capture_sha256"]:
        raise TemplateError("saved capture digest mismatch; recapture source")
    previous = {d["source_name"]: d["classification_evidence"]
                for d in record.get("topology", {}).get("devices", [])
                if d.get("classification_evidence")}
    metadata = dict(category=record["category"], provenance=record["provenance"])
    rebuilt = make_template(capture, classifications=previous, **metadata)
    if record["schema_version"] == SCHEMA_V3:
        from .template_reuse_build import make_reusable

        rebuilt = make_reusable(rebuilt, record["reuse_contract"], {
            d["id"]: d["classification_evidence"] for d in rebuilt["topology"]["devices"]
        }, granularity=record["granularity"])
    if rebuilt != record:
        raise TemplateError("saved reference does not match retained capture and evidence")
    supplied = args.get("classifications", {})
    if any(name in previous and previous[name] != value for name, value in supplied.items()):
        raise TemplateError("completion cannot replace pinned classification evidence")
    options = dict(reuse=args["reuse"], classifications=previous | supplied)
    result = store_capture(catalog, capture, capture_bytes, options, **metadata)
    return result | dict(completed_from=record["template_ref"], live=False)
