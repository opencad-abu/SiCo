"""Revalidate bound private evidence before adapting standard data to legacy engines."""

import json
from pathlib import Path

from ..template_capture import parse_capture, read_bytes
from ..template_schema import TemplateError, TemplateUnavailable, canonical
from .codec import relative, sha
from .identity import reference


def replay(item):
    from .collect import compile_package

    if item["entry"]["detail_level"] != "raw":
        raise TemplateUnavailable(
            "public projection is an offline reference; select raw for retained execution"
        )
    source = item["tables"]["sources"]["items"].get("intent", {})
    value = source.get("evidence_ref", "")
    if not value.startswith("collection-request:"):
        raise TemplateUnavailable("standard execution needs supported retained collection evidence")
    key = value.split(":")[1]
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise TemplateError("invalid retained request identity")
    root = item["roots"].get("private")
    if not root or not Path(root).is_absolute():
        raise TemplateUnavailable("retained evidence resource root is unavailable")
    request = json.loads(read_bytes(relative(root, "evidence/" + key + ".request.json")))
    scope = request["scope"]
    if sha(canonical(scope).encode())[7:] != key or sha(canonical(scope).encode()) != source.get(
        "scope_digest", sha(canonical(scope).encode())
    ):
        raise TemplateError("saved request digest mismatch")
    raw = read_bytes(relative(root, "captures/" + scope["capture_sha256"] + ".jsonl"))
    capture = parse_capture(raw)
    if capture["sha256"] != scope["capture_sha256"]:
        raise TemplateError("saved capture digest mismatch")
    package, tables, docs, record, _, report = compile_package(
        capture, scope["options"], request, key
    )
    if reference(package, tables, docs, "main") != item["entry"]["template_ref"]:
        raise TemplateError("saved reference does not match retained capture and evidence")
    if docs["qualification"] != item["documents"]["qualification"]:
        raise TemplateError("qualification receipt differs from executed validator")
    report_key = sha(report)[7:]
    if read_bytes(relative(root, "evidence/" + report_key + ".validation.json")) != report:
        raise TemplateError("qualification report digest mismatch")
    return record, capture, raw, scope


def engine_record(item):
    """Transient engine input, never persisted or presented as another data authority."""
    record, _, _, _ = replay(item)
    ref = item["entry"]["template_ref"]
    record["template_ref"] = ref
    record["summary"]["template_ref"] = ref
    record["standard_revision"] = item["observed"]["package.json"]
    record["standard_capabilities"] = {
        k: row["result"] for k, row in item["documents"]["qualification"]["checks"].items()
    }
    record["standard_use"] = item["entry"]["use"]
    return record


def require_execution(record):
    """Every creation path checks the selected standard receipt, including direct prepare."""
    checks = record.get("standard_capabilities")
    if checks is None:
        return
    if (
        record["standard_use"]["reuse"] != "allow"
        or checks["placement-reference-v1"] != "passed"
        or not any(checks[p] == "passed" for p in ("whole-reuse-v1", "core-reuse-v1"))
    ):
        raise TemplateError("standard creation requires allowed reuse and qualified placement")


def require_symbol(record, style):
    """Admit only the standard reference capabilities consumed by the chosen style."""
    if not record["template_ref"].startswith("ctpl_"):
        return
    checks, use = record.get("standard_capabilities"), record.get("standard_use")
    if not isinstance(checks, dict) or not isinstance(use, dict):
        raise TemplateError("standard symbol requires retained admission metadata")
    if use.get("reference") != "allow":
        raise TemplateError("standard symbol requires allowed reference use")
    if style == "template_outline" and checks.get("symbol-reference-v1") != "passed":
        raise TemplateError("standard symbol requires passed symbol-reference-v1")
