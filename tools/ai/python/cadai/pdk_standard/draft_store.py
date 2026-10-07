"""Immutable draft generations with compare-and-swap heads and bounded pages."""

import uuid

from sicostate import project_directory

from ..pdk_normalize import now
from .draft_contract import FORMAT, reference
from .jsonio import LIMIT, Writer, atomic, encode, fail, mapping, read, relative
from .package import locked


class Store:
    def __init__(self, workspace):
        self.workspace = workspace
        self.root = project_directory(workspace, "ai/pdk-drafts")

    def directory(self, ref):
        return relative(self.root, reference(ref))

    def load(self, ref):
        root = self.directory(ref)
        head = read(root / "head.json")
        state = read(relative(root, head["path"]), head["digest"])
        if state.get("format") != FORMAT or state.get("draft_ref") != ref:
            fail("Invalid PDK mapping draft", "pdk_update_conflict")
        state["entries"] = mapping(root, state["entries"])
        state["batches"] = mapping(root, state["batches"])
        return state

    def save(self, state, expected=None):
        project_directory(self.workspace, "ai/pdk-drafts", create=True)
        root = self.directory(state["draft_ref"])
        with locked(root):
            head = root / "head.json"
            if head.exists():
                current = self.load(state["draft_ref"])
                if current["version"] != expected:
                    fail("Concurrent mapping draft change; read again", "pdk_update_conflict")
            elif expected is not None:
                fail("Mapping draft disappeared", "pdk_update_conflict")
            state["version"] = uuid.uuid4().hex
            state["updated_at"] = now()
            name = state["version"] + ".json"
            writer = Writer(root)
            digest = writer.put(name, state, ("entries", "batches"))
            atomic(head, {"path": name, "digest": digest})
        return state

    def list(self, library=None):
        result = {}
        if self.root.exists():
            for path in sorted(self.root.glob("*/head.json")):
                ref = "pdk-draft:" + path.parent.name
                state = self.load(ref)
                if library and state["library"] != library:
                    continue
                result[ref] = {k: state[k] for k in ("draft_ref", "library", "status", "version")}
        return result


def page(rows, metadata, args, *, prefix="pdk-draft"):
    from .jsonio import fingerprint

    query = {k: v for k, v in args.items() if k != "cursor"}
    token = fingerprint([rows, metadata, query])[7:31]
    offset = 0
    if args.get("cursor"):
        parts = args["cursor"].split(":")
        if len(parts) != 3 or parts[:2] != [prefix, token] or not parts[2].isdigit():
            fail("Draft cursor is stale", "cursor_mismatch")
        offset = int(parts[2])
    if offset > len(rows):
        fail("Draft cursor is out of range", "cursor_mismatch")
    response = {**metadata, "items": [], "total": len(rows), "complete": False}
    for row in rows[offset : offset + args.get("page_size", 20)]:
        if len(encode({**response, "items": response["items"] + [row]})) > LIMIT - 512:
            break
        response["items"].append(row)
    if offset < len(rows) and not response["items"]:
        fail("One mapping record exceeds the response budget", "pdk_data_limit")
    end = offset + len(response["items"])
    response.update(returned=len(response["items"]), complete=end == len(rows))
    if end < len(rows):
        response["next_cursor"] = f"{prefix}:{token}:{end}"
    return response
