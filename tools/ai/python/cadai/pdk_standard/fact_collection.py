"""Generation-only objective fact collection over the common cumulative overlay."""

from sicostate import project_directory

from ..pdk_errors import PdkUnavailable
from . import VERSION
from .fact_import import Candidate, validate_sources
from .fact_reports import FactReports
from .jsonio import fail
from .package import locked
from .parameter_facts import ParameterFacts
from .provenance import verify
from .workspace import select


class CollectFacts:
    def __init__(self, workspace, environment, bridge):
        self.workspace, self.environment, self.bridge = workspace, environment, bridge
        self.reports = FactReports(workspace)

    def run(self, args):
        path, lock = self.reports.request(args)
        with lock:
            ref = self.reports.reserve(path, args)
            current = verify(select(self.workspace, args["library"], self.environment))
            recovered = self.reports.recovery(ref, current)
            if recovered:
                return recovered
            if current.revision != args["revision"]:
                fail("Effective revision changed before fact collection", "pdk_update_conflict")
            plan = ParameterFacts(current, args["facts"], ref)
            try:
                plan.plan()
                candidate = verify(Candidate(current, plan))
            except PdkUnavailable as exc:
                result = self.response(ref, current.revision, 0, 0, 0, 0, "conflict")
                result["error"] = {"fact_id": plan.active_id, "code": exc.code, "message": str(exc)}
                self.reports.save(
                    ref,
                    {"library": args["library"], "revision": current.revision, "summary": result},
                    args["facts"],
                )
                self.reports.finish(ref, result)
                return result
            evidence = project_directory(
                self.workspace, "ai/pdk-evidence/parameter-facts-" + ref.split(":")[1], create=True
            )
            try:
                captures = validate_sources(candidate, plan, self.bridge, evidence, self.workspace)
            except PdkUnavailable as exc:
                result = self.response(ref, current.revision, 0, 0, 0, 0, "conflict")
                result["error"] = {"code": exc.code, "message": str(exc)}
                self.reports.save(
                    ref,
                    {"library": args["library"], "revision": current.revision, "summary": result},
                    plan.records,
                )
                self.reports.finish(ref, result)
                return result
            contextual = sum(r["disposition"] == "context" for r in plan.records)
            unresolved = sum(r["disposition"] == "unresolved" for r in plan.records)
            result = self.response(
                ref,
                current.revision,
                len(plan.fields),
                contextual,
                unresolved,
                captures,
                "incomplete" if unresolved else "ok",
            )
            self.reports.save(
                ref,
                {
                    "library": args["library"],
                    "revision": current.revision,
                    "summary": result,
                    "capture_ref": str(evidence),
                },
                plan.records,
                plan.fields,
            )
            root = project_directory(self.workspace, "ai/pdk-data", create=True)
            try:
                with locked(root):
                    if (
                        select(self.workspace, args["library"], self.environment).revision
                        != current.revision
                    ):
                        fail("Concurrent fact update; read again", "pdk_update_conflict")
                    from .resource_check import check

                    for dep, record in plan.dependencies.items():
                        if record["kind"] in {"file", "model"}:
                            check(candidate, dep)
                    if plan.documents:
                        from .overlay import publish

                        result["revision"] = publish(root, current, plan.documents)["revision"]
            except PdkUnavailable as exc:
                result.update(
                    status="conflict",
                    collected_fields=0,
                    error={"code": exc.code, "message": str(exc)},
                )
            self.reports.finish(ref, result)
            return result

    @staticmethod
    def response(ref, revision, count, contextual, unresolved, captures, status):
        return {
            "schema_version": VERSION,
            "status": status,
            "report_ref": ref,
            "revision": revision,
            "collected_fields": count,
            "context_records": contextual,
            "unresolved_records": unresolved,
            "live_captures": captures,
            "usage_rules_changed": False,
            "callbacks_executed": False,
            "oa_writes": False,
            "next_action": "get_pdk_fact_report",
        }
