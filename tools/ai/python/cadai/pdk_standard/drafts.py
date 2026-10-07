"""Recoverable manual-led mapping batches over the authoritative update workflow."""

import uuid
from copy import deepcopy

from sicostate import project_directory

from ..pdk_errors import PdkUnavailable
from . import VERSION
from .draft_batch import request_digest, review, validate_changes
from .draft_contract import FORMAT, SOURCE, validate
from .draft_contract import entry as validate_entry
from .draft_sources import manual, observe, signature
from .draft_store import Store, page
from .jsonio import fail
from .package import locked
from .update_store import Reviews
from .workspace import select


class Drafts:
    def __init__(self, workspace, environment, bridge=None):
        self.workspace, self.environment, self.bridge = workspace, environment, bridge
        self.store, self.reviews = Store(workspace), Reviews(workspace)

    def _package(self, library, revision):
        package = select(self.workspace, library, self.environment)
        if package.revision != revision:
            fail("Effective PDK revision changed; read again", "pdk_update_conflict")
        return package

    def _progress(self, state, package):
        """Project receipts from the update owner; never create a second permission store."""
        batches = deepcopy(state["batches"])
        revisions = {state["revision"]}
        for batch in batches.values():
            if not batch.get("update_ref"):
                continue
            receipt = self.reviews.receipt(batch["update_ref"], package)
            if receipt:
                batch["status"] = "applied" if receipt["status"] == "ok" else "cancelled"
                if receipt.get("revision"):
                    revisions.add(receipt["revision"])
            else:
                batch["status"] = "review_pending"
        return batches, revisions

    def _state(self, ref):
        state = self.store.load(ref)
        try:
            package = select(self.workspace, state["library"], self.environment)
            batches, revisions = self._progress(state, package)
            source_ok = manual(package, state["source"]) == state["source_identity"]
            stale = not source_ok or package.revision not in revisions
            for row in state["entries"].values():
                if signature(package, row) != row["source_signature"]:
                    stale = True
            current = package.revision
        except PdkUnavailable as exc:
            batches, current, stale = deepcopy(state["batches"]), None, True
            state["source_error"] = {"code": exc.code, "message": str(exc)}
        state["batches"] = batches
        if state["status"] != "cancelled" and stale:
            state["status"] = "stale"
        return state, current

    def prepare(self, args):
        package = self._package(args["library"], args["revision"])
        validate(args["source"], SOURCE)
        source_identity = manual(package, args["source"])
        incoming = [validate_entry(row) for row in args["entries"]]
        ids = [row["entry_id"] for row in incoming]
        if len(ids) != len(set(ids)):
            fail("Duplicate incoming draft entry")
        ref = args.get("draft_ref")
        expected = args.get("draft_version")
        if ref:
            state, current = self._state(ref)
            if state["version"] != expected or state["library"] != args["library"]:
                fail("Draft revision or library changed", "pdk_update_conflict")
            if state["status"] == "cancelled" or state["source_identity"] != source_identity:
                fail(
                    "Cancelled draft or changed manual requires a new draft", "pdk_update_conflict"
                )
            if state["source"] != args["source"]:
                fail("Manual identity changed", "pdk_update_conflict")
            submitted = {
                i
                for b in state["batches"].values()
                if b["status"] != "cancelled"
                for i in b["entry_ids"]
            }
            if submitted & set(ids):
                fail("A submitted mapping cannot be rewritten", "pdk_update_conflict")
            for ident, row in state["entries"].items():
                if (
                    ident not in ids
                    and ident not in submitted
                    and signature(package, row) != row["source_signature"]
                ):
                    fail("Changed source mapping must be reviewed explicitly", "pdk_source_changed")
            if args.get("mode", "append") == "append" and set(ids) & set(state["entries"]):
                fail("Existing entry requires mode=upsert")
        else:
            ref = "pdk-draft:" + uuid.uuid4().hex
            state = {
                "format": FORMAT,
                "draft_ref": ref,
                "library": args["library"],
                "revision": args["revision"],
                "source": deepcopy(args["source"]),
                "source_identity": source_identity,
                "status": "open",
                "entries": {},
                "batches": {},
            }
        if source_identity["state"] == "unavailable" and any(
            r["status"] == "candidate" for r in incoming
        ):
            fail("Missing manual must remain unresolved; no automatic authorization")
        evidence = project_directory(
            self.workspace,
            "ai/pdk-evidence/" + ref.replace(":", "-") + "/" + uuid.uuid4().hex,
            create=True,
        )
        observed = observe(
            package, incoming, self.bridge, {"workspace": self.workspace, "root": evidence}
        )
        state["entries"].update({row["entry_id"]: row for row in observed})
        state.update(status="open", revision=args["revision"])
        state.pop("source_error", None)
        # Progress statuses are projections except for explicit no-change reuse.
        for batch in state["batches"].values():
            if batch.get("update_ref"):
                batch["status"] = "review_pending"
        self.store.save(state, expected)
        return self.get({"draft_ref": ref})

    def get(self, args):
        if not args.get("draft_ref"):
            rows = list(self.store.list(args.get("library")).values())
            return page(
                rows, {"schema_version": VERSION, "status": "ok", "section": "drafts"}, args
            )
        state, current = self._state(args["draft_ref"])
        section = args.get("section", "entries")
        batches = deepcopy(state["batches"])
        for row in state["entries"].values():
            batches.setdefault(row["batch_id"], {"batch_id": row["batch_id"], "status": "open"})
        rows = list(batches.values()) if section == "batches" else list(state["entries"].values())
        metadata = {
            "schema_version": VERSION,
            "status": "ok",
            "draft_status": state["status"],
            "draft_ref": state["draft_ref"],
            "draft_version": state["version"],
            "library": state["library"],
            "revision": state["revision"],
            "current_revision": current,
            "source": state["source_identity"],
            "section": section,
        }
        if state.get("source_error"):
            metadata["source_error"] = state["source_error"]
        return page(
            sorted(rows, key=lambda r: r["batch_id"] if section == "batches" else r["entry_id"]),
            metadata,
            args,
        )

    def cancel(self, args):
        # Same lock as update application prevents cancel racing with the index switch.
        root = project_directory(self.workspace, "ai/pdk-data", create=True)
        with locked(root):
            state = self.store.load(args["draft_ref"])
            if state["status"] != "cancelled":
                if state["version"] != args["draft_version"]:
                    fail("Draft changed before cancellation", "pdk_update_conflict")
                state["status"] = "cancelled"
                self.store.save(state, args["draft_version"])
        return self.get({"draft_ref": args["draft_ref"]})

    def submit(self, args, updates):
        state, current = self._state(args["draft_ref"])
        if state["version"] != args["draft_version"]:
            fail("Mapping draft changed; read again", "pdk_update_conflict")
        if state["status"] != "open":
            fail("Only an open draft can submit a batch", "pdk_update_unavailable")
        digest = request_digest(args)
        old = state["batches"].get(args["batch_id"])
        if old and old["status"] != "cancelled":
            if digest != old["request_digest"]:
                fail("Batch already submitted with different contents", "pdk_update_conflict")
            if old.get("update_ref"):
                return updates.restore(old["update_ref"])
            package = self._package(state["library"], args["revision"])
            self._validate_reuse(updates, package, args["changes"])
            return self._reused(state, old)
        package = self._package(state["library"], args["revision"])
        if current != args["revision"]:
            fail("Batch effective revision changed", "pdk_update_conflict")
        selected = [state["entries"].get(i) for i in args["entry_ids"]]
        if (
            not selected
            or len(args["entry_ids"]) != len(set(args["entry_ids"]))
            or any(r is None or r["batch_id"] != args["batch_id"] for r in selected)
        ):
            fail("Unknown or mismatched batch entries")
        if any(r["status"] != "candidate" or r.get("difference") for r in selected):
            fail("Unresolved mappings cannot enter confirmation")
        if state["source_identity"]["state"] != "verified":
            fail("Batch requires a verified manual", "pdk_source_changed")
        pending, reused = validate_changes(package, selected, args["changes"])
        mapping = review(state, selected, args["batch_id"], reused)
        batch = {
            "batch_id": args["batch_id"],
            "entry_ids": args["entry_ids"],
            "request_digest": digest,
        }
        if pending:
            result = updates.prepare(
                {"library": state["library"], "revision": args["revision"], "changes": pending},
                review_context={"mapping": mapping},
            )
            batch.update(status="review_pending", update_ref=result["update_ref"])
        else:
            self._validate_reuse(updates, package, args["changes"])
            batch.update(status="reused", revision=package.revision)
            result = self._reused(state, batch)
        state["revision"] = args["revision"]
        state["batches"][args["batch_id"]] = batch
        self.store.save(state, args["draft_version"])
        return result

    @staticmethod
    def _validate_reuse(updates, package, changes):
        if updates.source_validator:
            updates.source_validator(
                package,
                [
                    {
                        "file": r["file"],
                        "changes": [
                            {"path": op["path"], "after": op.get("value")} for op in r["ops"]
                        ],
                    }
                    for r in changes
                ],
            )

    def require_review(self, package, result):
        mapping = result["mapping"]
        state, current = self._state(mapping["draft_ref"])
        if state["status"] != "open" or current != package.revision:
            fail("Mapping draft cancelled or stale", "pdk_update_conflict")
        batch = state["batches"].get(mapping["batch_id"], {})
        if batch.get("update_ref") != result["update_ref"]:
            fail("Review no longer belongs to this mapping batch", "pdk_update_conflict")
        selected = [state["entries"][ident] for ident in batch["entry_ids"]]
        if review(state, selected, mapping["batch_id"], mapping["reused"]) != mapping:
            fail("Reviewed mapping changed", "pdk_update_conflict")

    @staticmethod
    def _reused(state, batch):
        return {
            "schema_version": VERSION,
            "status": "reused",
            "draft_ref": state["draft_ref"],
            "batch_id": batch["batch_id"],
            "revision": batch["revision"],
            "confirmation_required": False,
            "next_action": "get_pdk_parameter_draft",
        }
