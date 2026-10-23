"""Validate saved DD file hashes and actual interfaces without reading mutable source paths."""

import re

from .template_schema import TemplateError

SCHEMA = "cad.template.native-content.v1"
METHOD = "oa-dd-static-content-v1"
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def validate(header, rows, footer, *, capture_v2):
    before = header.get("native_content")
    after = footer.get("native_content_after")
    if before is None and after is None:
        return
    if not capture_v2 or before != after:
        raise TemplateError("native content changed or lacks guarded capture v2 evidence")
    if (
        not isinstance(before, dict)
        or set(before) != {"schema", "views"}
        or before["schema"] != SCHEMA
    ):
        raise TemplateError("unsupported native content schema")
    views = before["views"]
    if not isinstance(views, list) or not 1 <= len(views) <= 513:
        raise TemplateError("native content view budget")
    found = {}
    for view in views:
        fields = {
            "target",
            "role",
            "library_path",
            "view_type",
            "parameterized",
            "instance_count",
            "mosaic_count",
            "files",
            "terminals",
        }
        if not isinstance(view, dict) or set(view) != fields:
            raise TemplateError("invalid native view fields")
        target = view["target"]
        if (
            not isinstance(target, list)
            or len(target) != 3
            or any(not isinstance(s, str) or not s for s in target)
        ):
            raise TemplateError("invalid native target")
        if not isinstance(view["role"], str):
            raise TemplateError("invalid native role")
        key = (view["role"], tuple(target))
        if key in found or key[0] not in {"source", "master"}:
            raise TemplateError("duplicate/invalid native target")
        found[key] = view
        if (
            type(view["parameterized"]) is not bool
            or any(
                type(view[k]) is not int or view[k] < 0 for k in ("instance_count", "mosaic_count")
            )
            or not isinstance(view["library_path"], str)
            or not view["library_path"].startswith("/")
            or not isinstance(view["view_type"], str)
        ):
            raise TemplateError("invalid native view observations")
        files = view["files"]
        if not isinstance(files, list) or len(files) > 64:
            raise TemplateError("native file inventory budget")
        names = set()
        for row in files:
            if (
                not isinstance(row, dict)
                or set(row) != {"name", "sha256"}
                or not isinstance(row["name"], str)
                or not row["name"]
                or row["name"] in {".", ".."}
                or "/" in row["name"]
                or row["name"] in names
                or not isinstance(row["sha256"], str)
                or not DIGEST.fullmatch(row["sha256"])
            ):
                raise TemplateError("invalid native file identity")
            names.add(row["name"])
        terms = view["terminals"]
        if not isinstance(terms, list) or len(terms) > 4096:
            raise TemplateError("native terminal budget")
        names = set()
        for row in terms:
            if (
                not isinstance(row, dict)
                or set(row) != {"name", "direction", "width"}
                or not isinstance(row["name"], str)
                or not row["name"]
                or row["name"] in names
                or type(row["width"]) is not int
                or row["width"] < 1
                or not isinstance(row["direction"], str)
            ):
                raise TemplateError("invalid native interface")
            names.add(row["name"])
    source = tuple(header[k] for k in ("lib", "cell", "view"))
    instances = [r for r in rows if r["kind"] == "instance"]
    expected = {("source", source)} | {
        ("master", tuple(r[k] for k in ("libName", "cellName", "viewName"))) for r in instances
    }
    if found.keys() != expected:
        raise TemplateError("native source/master coverage differs")
    own = found[("source", source)]
    terminals = [
        dict(name=r["name"], direction=r.get("direction"), width=r.get("numBits"))
        for r in rows
        if r["kind"] == "terminal"
    ]
    if (
        sorted(own["terminals"], key=lambda r: r["name"])
        != sorted(terminals, key=lambda r: r["name"])
        or own["instance_count"] != len(instances)
        or own["mosaic_count"] != sum(r["kind"] == "mosaic" for r in rows)
        or own["library_path"] != header["library_path"]
    ):
        raise TemplateError("native source observations differ")
    for row in instances:
        master = found[("master", tuple(row[k] for k in ("libName", "cellName", "viewName")))]
        if master["parameterized"]:
            # A parameterized master evaluates per instance; its saved interface is
            # one evaluation, so the per-instance terminals stay observed facts
            # instead of a cross-check against a single saved interface.
            continue
        if set(r["name"] for r in master["terminals"]) != set(row.get("terminal_names") or []):
            raise TemplateError("native master interface differs from observed instance")


def fingerprint(view):
    """Static direct DD view only; file bytes include saved properties and pin figures."""
    from .template_standard.codec import jcs, sha

    if (
        view["parameterized"]
        or view["mosaic_count"]
        or not any(r["name"].endswith(".oa") for r in view["files"])
        or (view["role"] == "master" and view["instance_count"])
    ):
        return None
    # Source role and site paths are not part of native view identity.
    value = {k: view[k] for k in ("target", "view_type", "instance_count", "mosaic_count")}
    value["files"] = sorted(view["files"], key=lambda r: r["name"])
    value["terminals"] = sorted(view["terminals"], key=lambda r: r["name"])
    return sha(jcs(dict(method=METHOD, **value)))
