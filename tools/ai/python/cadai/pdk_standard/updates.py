"""Reviewed cumulative workspace overlays; publication requires host answer evidence."""

import uuid
from copy import deepcopy
from pathlib import Path

from sicostate import project_directory

from ..pdk_normalize import now
from . import VERSION, validate
from .jsonio import fail, sha
from .package import locked, logical
from .patches import apply as apply_patch
from .patches import node
from .review import validate_scope
from .update_store import Reviews
from .update_store import result as review_result
from .workspace import select


def expected(value):
    try:
        import rfc8785
    except ImportError:
        fail(
            "Install declared dependency rfc8785==0.1.4 before standard updates",
            "pdk_dependency_unavailable",
        )
    try:
        return "sha256-jcs:" + sha(rfc8785.dumps(value))[7:]
    except rfc8785.CanonicalizationError as exc:
        fail("Value cannot be represented as RFC 8785 JCS: " + str(exc))


def apply_change(package, name, change):
    original = package.base.document(name) if name in package.base.manifest["files"] else None
    if change.get("expected") != (expected(original) if original is not None else "absent"):
        fail("Overlay target changed: " + name, "pdk_update_conflict")
    for ref, digest in change.get("dependencies", {}).items():
        if package.manifest["dependencies"].get(ref, {}).get("fingerprint") != digest:
            fail("Overlay dependency changed", "pdk_update_conflict")
    if (
        set(change) - {"expected", "dependencies", "ops", "replacement_ref", "delete_file"}
        or sum(k in change for k in ("ops", "replacement_ref", "delete_file")) != 1
    ):
        fail("Invalid overlay change record")
    if "ops" in change:
        value = apply_patch(original, change["ops"])
    elif "replacement_ref" in change:
        value = logical(package.overlay, change["replacement_ref"], logical_name=name)
    else:
        fail("File deletion is unsupported by this consumer", "unsupported_pdk_standard")
    validate.document(name, value)
    return value


def difference(old, new, path=""):
    if old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        rows = []
        for key in sorted(set(old) | set(new)):
            pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in old:
                rows.append({"path": pointer, "after": new[key]})
            elif key not in new:
                rows.append({"path": pointer, "before": old[key], "removed": True})
            else:
                rows.extend(difference(old[key], new[key], pointer))
        return rows
    return [{"path": path, "before": old, "after": new}]


def _compute(package, args):
    """Rebuild the exact logical documents and bounded review differences."""
    changes, diffs = {}, []
    devices = package.document("device.json")["items"]
    allowed = {"device.json"} | {
        r["dir"] + "/" + suffix
        for r in devices.values()
        if "dir" in r
        for suffix in ("cdf.json", "symbol.json")
    }
    for change in args["changes"]:
        name = change["file"]
        if name not in allowed or name in changes:
            fail("Update must select distinct collected device/CDF/symbol files")
        old = package.document(name)
        value = apply_patch(old, change["ops"])
        validate.document(name, value)
        # Provenance is assigned by the host after a real answer, never accepted from model input.
        if any(old.get(k) != value.get(k) for k in ("source", "depends_on", "evidence")):
            fail("Update cannot supply or edit its own confirmation evidence")
        if name == "device.json":
            if set(old["items"]) != set(value["items"]):
                fail("Usage updates cannot invent or remove observed cells")
            for key in old["items"]:
                if any(
                    old["items"][key].get(k) != value["items"][key].get(k)
                    for k in ("library", "cell", "view", "dir")
                ):
                    fail("Usage update cannot change observed device identity")
        elif name.endswith("/cdf.json") and (
            set(old["parameters"]) != set(value["parameters"])
            or old["presence"] != value["presence"]
        ):
            fail("CDF update cannot invent/remove observed parameters or presence")
        elif name.endswith("/symbol.json"):
            if any(old[k] != value[k] for k in ("coordinate_system", "unit", "parameterized")):
                fail("Symbol update cannot change observed geometry identity")
            if old.get("x_geometry_collection") != value.get("x_geometry_collection"):
                fail("Collection marker is assigned only by the source collector")
            if set(old["terminals"]) != set(value["terminals"]):
                fail("Symbol update cannot invent terminals")
        diff = difference(old, value)
        for operation in change["ops"]:
            path = operation["path"]
            if operation["op"] == "replace" and node(old, path) == node(value, path):
                diff.append({"path": path, "before": node(old, path), "after": node(value, path)})
        if diff:
            changes[name] = value
            diffs.append({"file": name, "changes": diff})
    if not diffs:
        fail("No PDK data changes to confirm")
    return changes, diffs


class Updates:
    def __init__(self, workspace, environment):
        self.workspace, self.environment = Path(workspace), environment
        self.confirmation = None
        self.source_validator = None
        self.previews = {}
        self.reviews = Reviews(workspace)

    def prepare(self, args, *, review_context=None):
        package = select(self.workspace, args["library"], self.environment)
        if package.revision != args["revision"]:
            fail("Effective package changed; read again", "pdk_update_conflict")
        changes, diffs = _compute(package, args)
        scope = {"library": args["library"], "revision": package.revision, "changes": diffs}
        if review_context:
            if not isinstance(review_context, dict) or set(review_context) - {"mapping"}:
                fail("Invalid PDK review context")
            scope.update(deepcopy(review_context))
        validate_scope(scope)
        if self.source_validator:
            self.source_validator(package, diffs)
        ref = "pdk-update:" + uuid.uuid4().hex
        result = review_result(ref, scope)
        if len(self.previews) >= 32:
            completed = next(
                (key for key, row in self.previews.items() if row.get("applied")), None
            )
            if completed:
                del self.previews[completed]
            else:
                fail("Too many pending update previews")
        evidence, digest = self.reviews.save(ref, args, scope)
        self.previews[ref] = {
            "args": deepcopy(args),
            "documents": changes,
            "result": result,
            "scope": evidence,
            "digest": digest,
        }
        return deepcopy(result)

    def restore(self, ref):
        """Reconstruct a pending review without granting or replaying confirmation."""
        args, scope, digest = self.reviews.read(ref)
        package = select(self.workspace, args["library"], self.environment)
        receipt = self.reviews.receipt(ref, package)
        if receipt:
            return receipt
        if package.revision != args["revision"]:
            fail("Concurrent PDK update or baseline change", "pdk_update_conflict")
        changes, diffs = _compute(package, args)
        expected_scope = {
            "library": args["library"],
            "revision": args["revision"],
            "changes": diffs,
        }
        if "mapping" in scope:
            expected_scope["mapping"] = scope["mapping"]
        if expected_scope != scope:
            fail("Review evidence changed", "pdk_update_conflict")
        preview = {
            "args": args,
            "documents": changes,
            "result": review_result(ref, scope),
            "scope": self.reviews.path(ref),
            "digest": digest,
        }
        self.previews[ref] = preview
        return deepcopy(preview["result"])

    def apply(self, args):
        ref = args["update_ref"]
        with self.reviews.lock(ref):
            if ref not in self.previews:
                restored = self.restore(ref)
                if not restored.get("confirmation_required"):
                    return restored
            preview = self.previews[ref]
            receipt = self.reviews.receipt(ref)
            if receipt:
                return receipt
            answer = self.confirmation(preview["result"]) if self.confirmation else None
            if not answer:
                return deepcopy(preview["result"])
            self.reviews.check(preview)
            if answer.get("decision") != "confirm":
                result = {"schema_version": VERSION, "status": "cancelled", "update_ref": ref}
                self.reviews.finish(ref, result)
                preview["applied"] = result
                return result
            if not answer.get("actor") or not answer.get("evidence_ref"):
                fail("Host confirmation lacks answer evidence")
            root = project_directory(self.workspace, "ai/pdk-data", create=True)
            with locked(root):
                package = select(self.workspace, preview["args"]["library"], self.environment)
                receipt = self.reviews.receipt(ref, package)
                if receipt:
                    return receipt
                if package.revision != preview["args"]["revision"]:
                    fail("Concurrent PDK update or baseline change", "pdk_update_conflict")
                if preview["result"].get("mapping"):
                    from .drafts import Drafts

                    Drafts(self.workspace, self.environment).require_review(
                        package, preview["result"]
                    )
                if self.source_validator:
                    self.source_validator(package, preview["result"]["changes"])
                result = commit(root, package, preview, answer)
            self.reviews.finish(ref, result)
            preview["applied"] = result
            return result


def commit(root, package, preview, answer):
    revision = uuid.uuid4().hex
    documents = deepcopy(preview["documents"])
    source_id = "confirm_" + revision
    sources = deepcopy(package.document("sources.json"))
    sources["items"][source_id] = {
        "kind": "user",
        "at": now(),
        "summary": "Confirmed reviewed PDK data changes",
        "confirmed_by": answer["actor"],
        "scope_ref": str(preview["scope"]),
        "scope_digest": preview["digest"],
        "evidence_ref": answer["evidence_ref"],
    }
    documents["sources.json"] = sources
    for diff in preview["result"]["changes"]:
        value = documents[diff["file"]]
        mapping = preview["result"].get("mapping")
        if mapping:
            dependency = mapping["source"]["dependency"]
            if dependency not in value["depends_on"]:
                value["depends_on"].append(dependency)
        evidence = value.setdefault("evidence", {})
        for row in diff["changes"]:
            # Deletion is evidenced by the retained review, not by granting authority
            # to unchanged siblings under the removed node's parent.
            path = row["path"]
            for old in list(evidence):
                if old == path or old.startswith(path + "/"):
                    del evidence[old]
            if not row.get("removed"):
                evidence[path] = source_id
    from .overlay import publish

    return {**publish(root, package, documents), "update_ref": preview["result"]["update_ref"]}
