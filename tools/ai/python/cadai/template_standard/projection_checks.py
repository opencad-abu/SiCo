"""Reject noncanonical public projections across the entire published closure."""

import copy
import datetime
import re

from ..template_native_content import METHOD as NATIVE_METHOD
from ..template_rules import rule_ref
from .codec import fail
from .projection_content import documents, label
from .projection_sources import VALIDATOR, metadata
from .validate import DIGEST


def validate(package, tables, templates):
    entries = tables["template"]["items"]
    projected = [e for e in entries.values() if e["detail_level"] != "raw"]
    if not projected:
        return
    # A public package cannot hide raw entries, unused sources or resources next
    # to a sanitized closure. This writer/reader supports one projection per package.
    if (
        set(entries) != {"main"}
        or len(projected) != 1
        or set(tables) != {"sources", "template", "category", "file"}
    ):
        fail("public projection requires an isolated single-template package")
    entry, docs = entries["main"], templates["main"]
    level = entry["detail_level"]
    clean, _ = documents(docs, level, rekey=False)
    actual = {k: v for k, v in docs.items() if k != "qualification"}
    if clean != actual:
        fail("projection contains excluded or unsanitized semantic content")
    base = entry.get("derived_from", [])
    if (
        len(base) != 1
        or not isinstance(base[0], str)
        or not re.fullmatch(r"ctpl_[0-9a-f]{64}", base[0])
    ):
        fail("projection requires a fixed raw source reference")
    try:
        at = datetime.datetime.fromisoformat(package["created_at"])
        if at.tzinfo is None or at.isoformat() != package["created_at"]:
            raise ValueError()
    except (ValueError, TypeError):
        fail("projection timestamp must be canonical ISO 8601")
    dependencies = copy.deepcopy(package["dependencies"])
    capture = dependencies.pop("capture", {})
    digest = capture.get("fingerprint")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        fail("projection requires controlled evidence fingerprint")
    expected_masters = docs.get("topology", {}).get("masters", {})
    if set(dependencies) != set(expected_masters):
        fail("projection dependency closure contains excluded source/master identities")
    for row in dependencies.values():
        if (
            row["kind"] != "master"
            or row["method"] != NATIVE_METHOD
            or not isinstance(row["fingerprint"], str)
            or not DIGEST.fullmatch(row["fingerprint"])
        ):
            fail("projection master fingerprint is not publishable")
        if set(row["target"]) != {"library", "cell", "view"}:
            fail("projection master target is not publishable")
        for value in row["target"].values():
            label(value)
    expected_package, expected_tables = metadata(
        base[0], level, entry["use"], package["created_at"], digest, clean, dependencies
    )
    expected_package["rules"] = {}
    for key in docs.get("reuse", {}).get("allowed_rules", []):
        installed = rule_ref(key)
        expected_package["rules"][key] = dict(
            name=key,
            version=installed["version"],
            implementation_digest="sha256:" + installed["implementation_digest"],
        )
    expected_package.update(revision=package["revision"], files=package["files"])
    if package != expected_package:
        fail("projection contains nonpublic package metadata or unused dependencies")
    # Physical manifest paths carry no source names, even when their bytes are hashed.
    for path in package["files"]:
        if not re.fullmatch(r"(?:template/)?[a-z]+(?:\.[a-z_]+(?:\.[0-9]{4})+)?\.json", path):
            fail("projection manifest contains a nonpublic path")
    validation = tables["sources"]["items"].get("validation", {})
    evidence_ref = validation.get("evidence_ref", "")
    if not re.fullmatch(r"validation:[0-9a-f]{64}", evidence_ref):
        fail("projection requires an opaque validation receipt")
    expected_tables["sources"]["items"]["validation"] = dict(
        kind="validation",
        at=package["created_at"],
        summary="Independent sanitized projection capability checks",
        validator=VALIDATOR,
        evidence_ref=evidence_ref,
    )
    expected_tables["template"]["items"]["main"]["template_ref"] = entry["template_ref"]
    if tables != expected_tables:
        fail("projection source/resource/category closure is not sanitized")
    qual = docs["qualification"]
    if (
        set(qual) != {"source", "depends_on", "template_ref", "checks"}
        or qual["source"] != "validation"
        or qual["depends_on"] != ["capture"]
    ):
        fail("projection qualification envelope is not sanitized")
    from .projection import checks_for

    fallback = checks_for(None, docs, level, entry["use"])
    for profile, row in qual["checks"].items():
        expected = fallback[profile]
        if expected["result"] == "not_applicable" and row != expected:
            fail("projection must exclude this capability")
        if row["result"] in {"passed", "failed"}:
            expected = dict(
                result=row["result"],
                at=package["created_at"],
                validator=VALIDATOR,
                evidence_ref=evidence_ref,
            )
            if profile in {"whole-reuse-v1", "core-reuse-v1"} and entry["use"]["reuse"] != "allow":
                fail("projection reuse permission is unavailable")
        elif row["result"] == "unknown":
            # Validators emit a fixed vocabulary; arbitrary diagnostics remain private.
            allowed = {
                "Controlled source evidence unavailable",
                "Capability validation has not run",
                "Symbol interface/geometry has gaps",
                "Electrical evidence is incomplete or unsupported",
                "Source use decision does not allow reuse",
            }
            if row.get("reason") not in allowed:
                fail("projection qualification contains private diagnostic text")
            expected = dict(result="unknown", reason=row["reason"])
        if row != expected:
            fail("projection qualification contains nonpublic evidence")
