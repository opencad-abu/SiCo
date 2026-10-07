"""Durable CDF suites with immutable generations and no automatic write replay."""

import re
import uuid

from sicostate import project_directory

from .cdf_validation_contract import REF
from .draft_store import page
from .jsonio import TARGET, Writer, atomic, encode, fail, fingerprint, mapping, read, relative
from .package import locked


class Store:
    def __init__(self, workspace):
        self.root = project_directory(workspace, "ai/pdk-cdf-validation")

    def path(self, ref):
        if not isinstance(ref, str) or not re.fullmatch(REF["pattern"], ref):
            fail("Invalid validation reference")
        return self.root / ref.split(":")[1]

    def reserve(self, args):
        path = (
            self.root
            / "requests"
            / (fingerprint([args["library"], args["request_id"]])[7:] + ".json")
        )
        with locked(path.with_suffix(".lock")):
            if path.exists():
                receipt = read(path)
                if receipt["digest"] != fingerprint(args):
                    fail("Validation request ID conflicts", "pdk_update_conflict")
                return receipt["ref"]
            ref = "pdk-cdf:" + uuid.uuid4().hex
            atomic(path, {"digest": fingerprint(args), "ref": ref})
            return ref

    def load(self, ref):
        root = self.path(ref)
        head = read(root / "head.json")
        state = read(relative(root, head["path"]), head["digest"])
        state["cases"] = mapping(root, state["cases"])
        return state

    def save(self, state):
        root = self.path(state["validation_ref"])
        for key, case in state["cases"].items():
            if len(encode({key: case})) > TARGET:
                fail("Validation record exceeds 8 KiB: " + key)
        name = uuid.uuid4().hex + ".json"
        digest = Writer(root).put(name, state, ("cases",))
        atomic(root / "head.json", {"path": name, "digest": digest})

    def get(self, args):
        if bool(args.get("validation_ref")) == bool(args.get("library")):
            fail("Specify validation_ref or library")
        if "library" in args:
            states = [
                self.load("pdk-cdf:" + h.parent.name) for h in sorted(self.root.glob("*/head.json"))
            ]
            return page(
                [summary(s) for s in states if s["library"] == args["library"]],
                {"library": args["library"]},
                args,
                prefix="pdk-cdf",
            )
        s = self.load(args["validation_ref"])
        return page(list(s["cases"].values()), summary(s), args, prefix="pdk-cdf")


def summary(state):
    counts = {}
    for c in state["cases"].values():
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {k: state[k] for k in ("validation_ref", "library", "device", "revision", "status")} | {
        "counts": counts,
        "coverage": state.get("coverage"),
        "execution_applied": state.get("execution_applied", False),
        "next_action": "advance_pdk_cdf_validation"
        if state["status"] == "prepared"
        else "get_pdk_cdf_validation",
    }
