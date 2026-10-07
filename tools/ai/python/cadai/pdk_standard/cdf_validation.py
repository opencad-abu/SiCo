"""Generation-only durable suites; advance executes exactly one background case."""

from pathlib import Path

from sicostate import project_directory

from .cdf_validation_cases import prepare as prepare_cases
from .cdf_validation_native import Native, evidence
from .cdf_validation_result import classify, coverage
from .cdf_validation_store import Store, summary
from .draft_sources import signature
from .fact_import import validate_sources
from .jsonio import TARGET, encode, fail, fingerprint
from .package import locked
from .provenance import verify
from .workspace import select


class Validation:
    def __init__(self, workspace, environment, bridge):
        self.workspace, self.environment, self.bridge = workspace, environment, bridge
        self.store = Store(workspace)
        self.native = Native(bridge, workspace)

    def package(self, state):
        p = verify(select(self.workspace, state["library"], self.environment))
        if p.revision != state["revision"]:
            fail("Validation revision changed", "pdk_update_conflict")
        if (
            state.get("signature")
            and signature(p, {"device": state["device"]}) != state["signature"]
        ):
            fail("Validation source identity changed", "pdk_source_changed")
        row = p.document("device.json")["items"].get(state["device"], {})
        if "dir" not in row:
            fail("Validation requires an observed device")
        return p, row, p.document(row["dir"] + "/cdf.json")

    def sources(self, p, row, root):
        from types import SimpleNamespace

        refs = set()
        for suffix in ("cdf.json", "symbol.json", "simulation.json"):
            name = row["dir"] + "/" + suffix
            if p.available(name):
                refs.update(p.document(name)["depends_on"])
        plan = SimpleNamespace(dependencies={r: p.manifest["dependencies"][r] for r in refs})
        validate_sources(p, plan, self.bridge, root, self.workspace)

    def prepare(self, args):
        ref = self.store.reserve(args)
        root = self.store.path(ref)
        with locked(root):
            if (root / "head.json").exists():
                return summary(self.store.load(ref))
            p, row, cdf = self.package(args)
            cases = prepare_cases(cdf, args["order"], args["cases"])
            state = {k: args[k] for k in ("library", "device", "revision", "order", "request_id")}
            state.update(
                validation_ref=ref,
                status="prepared",
                cases=cases,
                signature=signature(p, {"device": args["device"]}),
                cdf_digest=fingerprint(cdf),
                target={
                    "library": p.manifest["libraries"][row["library"]]["name"],
                    "cell": row["cell"],
                    "view": row["view"],
                },
            )
            raw = project_directory(
                self.workspace, "ai/pdk-evidence/cdf-" + ref.split(":")[1], create=True
            )
            self.sources(p, row, raw / "prepare")
            state["evidence_root"] = str(raw)
            self.store.save(state)
            return summary(state)

    def advance(self, args):
        ref = args["validation_ref"]
        root = self.store.path(ref)
        with locked(root):
            state = self.store.load(ref)
            if state["status"] != "prepared":
                return summary(state)
            if any(c["status"] == "running" for c in state["cases"].values()):
                for case in state["cases"].values():
                    if case["status"] == "running":
                        case["status"] = "uncertain"
                state["status"] = "interrupted"
                self.store.save(state)
                return summary(state)
            pending = [c for c in state["cases"].values() if c["status"] == "pending"]
            if not pending:
                state["status"] = "complete"
                self.store.save(state)
                return summary(state)
            case = min(pending, key=lambda c: c["position"])
            p, row, cdf = self.package(state)
            raw = Path(state["evidence_root"]) / case["id"]
            # Persist intent before any native dispatch. An interrupted case is never replayed.
            case["status"] = "running"
            case["target"] = {
                "library": "SicoTest",
                "cell": "pdk_cdf_" + ref.split(":")[1][:16] + "_" + case["id"],
                "view": "schematic",
            }
            self.store.save(state)
            try:
                self.sources(p, row, raw / "before")
                inspected = self.native.inspect(
                    state["target"], sorted(set(state["order"] + case["observe"]))
                )
                case["inspection"] = evidence(raw, "inspection", inspected)
                result = self.native.probe(
                    inspected["cdf_ref"],
                    case,
                    state["order"],
                    case["target"],
                    ref.split(":")[1] + "_" + case["id"],
                    inspected,
                )
                artifact = evidence(raw, "probe", result)
                case.update(classify(result, case, artifact))
                self.sources(p, row, raw / "after")
                self.package(state)
            except Exception as exc:
                # Unknown transport outcomes retain the unique target and cannot be auto-retried.
                case.update(
                    status="uncertain", error=str(exc)[:1024], error_type=type(exc).__name__
                )
                state["status"] = "interrupted"
                evidence(raw, "failure", {"type": type(exc).__name__, "message": str(exc)})
            if case["status"] in {"unsafe", "unsupported"}:
                state["status"] = "unsupported"
            if (
                all(c["status"] not in {"pending", "running"} for c in state["cases"].values())
                and state["status"] == "prepared"
            ):
                state["status"] = "complete"
            state["coverage"] = coverage(cdf, state["cases"])
            if len(encode({case["id"]: case})) > TARGET:
                case.pop("trace_labels", None)
                case.pop("observed", None)
                case["observations_in_artifact"] = True
                state["coverage"] = coverage(cdf, state["cases"])
            self.store.save(state)
            return {**summary(state), "case": case}

    def cancel(self, args):
        with locked(self.store.path(args["validation_ref"])):
            state = self.store.load(args["validation_ref"])
            for case in state["cases"].values():
                if case["status"] == "pending":
                    case["status"] = "cancelled"
                elif case["status"] == "running":
                    case["status"] = "uncertain"
            state["status"] = "cancelled"
            self.store.save(state)
            return summary(state)

    def finish(self, args):
        from .cdf_validation_publish import publish, recovered

        with locked(self.store.path(args["validation_ref"])):
            state = self.store.load(args["validation_ref"])
            if state.get("execution_applied"):
                return summary(state)
            if state.get("finalizing"):
                revision = recovered(self.workspace, self.environment, state)
                if revision:
                    state.update(revision=revision, execution_applied=True, finalizing=False)
                    self.store.save(state)
                    return summary(state)
            p, row, cdf = self.package(state)
            state["coverage"] = coverage(cdf, state["cases"])
            if args.get("apply_execution"):
                if not state["coverage"]["execution_eligible"]:
                    fail(
                        "Execution coverage incomplete: " + str(state["coverage"]["missing"]),
                        "pdk_generation_incomplete",
                    )
                self.sources(p, row, Path(state["evidence_root"]) / "final")
                state["finalizing"] = True
                self.store.save(state)
                state["revision"] = publish(self.workspace, self.environment, p, row, cdf, state)
                state["execution_applied"] = True
                state["finalizing"] = False
            self.store.save(state)
            return summary(state)
