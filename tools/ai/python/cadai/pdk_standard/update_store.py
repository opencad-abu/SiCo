"""Durable exact update reviews, terminal receipts and interrupted-commit recovery."""

import re
from pathlib import Path

from sicostate import project_directory

from . import VERSION
from .jsonio import atomic, fail, read
from .package import locked
from .patches import node
from .provenance import source_for
from .review import question, validate_scope


def result(ref, scope):
    return {
        "schema_version": VERSION,
        "status": "incomplete",
        "update_ref": ref,
        "revision": scope["revision"],
        "changes": scope["changes"],
        "confirmation_required": True,
        "next_action": "ask_user_to_confirm_pdk_update",
        "selection_question": question(scope, ref),
        **({"mapping": scope["mapping"]} if "mapping" in scope else {}),
    }


class Reviews:
    def __init__(self, workspace):
        self.workspace = workspace
        self.root = project_directory(workspace, "ai/pdk-confirmations")

    def path(self, ref, suffix=""):
        if not isinstance(ref, str) or not re.fullmatch(r"pdk-update:[0-9a-f]{32}", ref):
            fail("Invalid update reference", "pdk_update_unavailable")
        return self.root / (ref.split(":")[1] + suffix + ".json")

    def lock(self, ref):
        if not self.path(ref, ".request").exists():
            fail("Update preview expired; prepare again", "pdk_update_unavailable")
        return locked(self.root / (ref.split(":")[1] + ".lock"))

    def save(self, ref, args, scope):
        validate_scope(scope)
        project_directory(self.workspace, "ai/pdk-confirmations", create=True)
        scope_path = self.path(ref)
        digest = atomic(scope_path, scope)
        args_digest = atomic(self.path(ref, ".args"), args)
        atomic(self.path(ref, ".request"), {"scope_digest": digest, "args_digest": args_digest})
        return scope_path, digest

    def read(self, ref):
        meta = read(self.path(ref, ".request"))
        from ..pdk_errors import PdkUnavailable

        try:
            scope = read(self.path(ref), meta["scope_digest"])
            args = read(self.path(ref, ".args"), meta["args_digest"])
        except PdkUnavailable as exc:
            fail("Review evidence changed: " + str(exc), "pdk_update_conflict")
        validate_scope(scope)
        return args, scope, meta["scope_digest"]

    def check(self, preview):
        ref = preview["result"]["update_ref"]
        args, scope, digest = self.read(ref)
        if (
            digest != preview["digest"]
            or args != preview["args"]
            or result(ref, scope) != preview["result"]
        ):
            fail("Review evidence changed", "pdk_update_conflict")
        return scope

    def finish(self, ref, value):
        _, _, digest = self.read(ref)
        atomic(self.path(ref, ".result"), {"scope_digest": digest, "result": value})

    def receipt(self, ref, package=None):
        args, scope, digest = self.read(ref)
        terminal = self.path(ref, ".result")
        if terminal.exists():
            record = read(terminal)
            if record.get("scope_digest") != digest:
                fail("Update result does not match its review", "pdk_update_conflict")
            return record["result"]
        if package is None or package.revision == args["revision"]:
            return None
        # The index may have switched immediately before the worker died. A user
        # source in that immutable overlay is the commit receipt, not another answer.
        sources = package.document("sources.json")["items"]
        ids = {
            key
            for key, row in sources.items()
            if row.get("kind") == "user"
            and row.get("scope_digest") == digest
            and Path(row.get("scope_ref", "")).name == self.path(ref).name
        }
        if not ids:
            return None
        for group in scope["changes"]:
            document = package.document(group["file"])
            for change in group["changes"]:
                if change.get("removed"):
                    from ..pdk_errors import PdkUnavailable

                    try:
                        node(document, change["path"])
                    except PdkUnavailable:
                        continue
                    fail("Committed update was superseded", "pdk_update_conflict")
                if (
                    source_for(document, change["path"]) not in ids
                    or node(document, change["path"]) != change["after"]
                ):
                    fail("Committed update was superseded", "pdk_update_conflict")
        value = {
            "schema_version": VERSION,
            "status": "ok",
            "revision": package.revision,
            "update_ref": ref,
            "recovered": True,
            "next_action": "get_pdk_data",
        }
        return value
