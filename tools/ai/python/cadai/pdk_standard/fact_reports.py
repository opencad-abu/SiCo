"""Immutable source-fact reports and restart-safe request receipts."""

import re
import uuid

from sicostate import project_directory

from .draft_store import page
from .jsonio import Writer, atomic, fail, fingerprint, mapping, read, relative
from .package import locked
from .patches import node
from .provenance import source_for


class FactReports:
    def __init__(self, workspace):
        self.workspace = workspace
        self.root = project_directory(workspace, "ai/pdk-facts")

    def path(self, ref):
        if not isinstance(ref, str) or not re.fullmatch(r"pdk-facts:[0-9a-f]{32}", ref):
            fail("Invalid fact report reference")
        return self.root / ref.split(":")[1]

    def request(self, args):
        project_directory(self.workspace, "ai/pdk-facts", create=True)
        name = fingerprint([args["library"], args["request_id"]])[7:]
        path = self.root / "requests" / (name + ".json")
        return path, locked(path.with_suffix(".lock"))

    def reserve(self, path, args):
        digest = fingerprint(args)
        if path.exists():
            value = read(path)
            if value["digest"] != digest:
                fail("Fact request ID already binds different contents", "pdk_update_conflict")
            return value["ref"]
        ref = "pdk-facts:" + uuid.uuid4().hex
        atomic(path, {"digest": digest, "ref": ref})
        return ref

    def save(self, ref, header, records, fields=()):
        root = self.path(ref)
        if (root / "head.json").exists():
            fail("Fact report is already frozen", "pdk_update_conflict")
        # Report contents are frozen before effective publication; only result is terminal.
        digest = Writer(root).put(
            "report.json",
            {
                **header,
                "report_ref": ref,
                "records": {r["id"]: r for r in records},
                "fields": {str(i): f for i, f in enumerate(fields)},
            },
            ("records", "fields"),
        )
        atomic(root / "head.json", {"path": "report.json", "digest": digest})

    def load(self, ref):
        root = self.path(ref)
        head = read(root / "head.json")
        return read(relative(root, head["path"]), head["digest"])

    def finish(self, ref, result):
        atomic(self.path(ref) / "result.json", result)

    def recovery(self, ref, package):
        root = self.path(ref)
        if (root / "result.json").exists():
            self.load(ref)
            return read(root / "result.json")
        if not (root / "head.json").exists():
            return None
        report = self.load(ref)
        fields = list(mapping(root, report["fields"]).values())
        if not fields:
            result = {**report["summary"], "recovered": True}
            self.finish(ref, result)
            return result
        if report["revision"] == package.revision:
            result = {
                **report["summary"],
                "status": "conflict",
                "collected_fields": 0,
                "error": {
                    "code": "pdk_update_conflict",
                    "message": "Interrupted before commit; review and submit a new request_id",
                },
            }
            self.finish(ref, result)
            return result
        for row in fields:
            document = package.document(row["file"])
            if (
                node(document, row["path"]) != row["value"]
                or source_for(document, row["path"]) != row["source"]
            ):
                fail(
                    "Interrupted fact commit conflicts with current revision", "pdk_update_conflict"
                )
        result = {**report["summary"], "revision": package.revision, "recovered": True}
        self.finish(ref, result)
        return result

    def get(self, args):
        if bool(args.get("report_ref")) == bool(args.get("library")):
            fail("Specify exactly one of report_ref and library")
        if "library" in args:
            rows = []
            for head in sorted(self.root.glob("*/head.json")):
                ref = "pdk-facts:" + head.parent.name
                report = self.load(ref)
                if report["library"] != args["library"]:
                    continue
                terminal = head.parent / "result.json"
                rows.append(
                    read(terminal)
                    if terminal.exists()
                    else {"report_ref": ref, "status": "prepared", "revision": report["revision"]}
                )
            return page(rows, {"library": args["library"]}, args, prefix="pdk-facts")
        root = self.path(args["report_ref"])
        report = self.load(args["report_ref"])
        result = (
            read(root / "result.json")
            if (root / "result.json").exists()
            else {"status": "prepared"}
        )
        return page(
            list(mapping(root, report["records"]).values()),
            {
                "report_ref": args["report_ref"],
                "library": report["library"],
                "revision": report["revision"],
                "result": result,
                "capture_ref": report.get("capture_ref"),
            },
            args,
            prefix="pdk-facts",
        )
