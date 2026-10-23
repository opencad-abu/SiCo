"""Pin one resource selection to a session and recover only evidenced legacy choices."""

from copy import deepcopy

from ..agent_resources import fingerprints, snapshot
from ..agent_resources.manifest import read_selection
from ..agent_resources.schema import KINDS, require_supported


def legacy_selection(records):
    """Never skip a newer failed/preview inventory to guess the last effective choice."""
    inventories = [(i, e["payload"]) for i, e in enumerate(records)
                   if e["kind"] == "codex.resources.updated"
                   and not is_legacy_preview(e["payload"])]
    bindings = [i for i, e in enumerate(records) if e["kind"] == "codex.thread"]
    if not inventories or not bindings:
        raise ValueError("旧会话缺少可还原的资源选择证据，请新建会话；元数据仍可管理")
    index, candidate = inventories[-1]
    checked = candidate.get("status") == "checked" and bindings[0] < index
    initial_empty = (candidate.get("status") == "pending" and bindings[0] > index
                     and candidate.get("configured") is False and candidate.get("rows") == [])
    if not (checked or initial_empty) or "configuration" in candidate:
        # A new-format inventory without its selected event is an incomplete journal.
        raise ValueError("旧会话最后一条资源记录不能证明实际选择，请新建会话")
    manifest = {"version": 2, **{kind: [] for kind in KINDS}}
    for row in candidate["rows"]:
        kind = row["kind"]
        if kind not in KINDS:
            raise ValueError("旧会话资源种类无法还原，请新建会话")
        fields = ("id", "path", "source", "access", "name") if kind == "plugins" else (
            "id", "path", "source", "access")
        manifest[kind].append({**{key: row[key] for key in fields}, "enabled": True})
    selection = {"path": candidate["manifest"], "source": "legacy",
                 "revision": candidate.get("sha256") or None, "manifest": manifest}
    return snapshot.create(selection, fingerprints.capture(candidate["rows"], legacy=True), origin="legacy")


def is_legacy_preview(payload):
    """Our read-only preview cannot erase the preceding legacy loading evidence."""
    config = payload.get("configuration")
    return (isinstance(config, dict) and config.get("state") == "preview"
            and config.get("origin") == "legacy")


class ResourceSelection:
    def __init__(self, backend, records):
        self.backend = backend
        self._stored = None
        self._legacy = []
        for event in records:
            if event["kind"] == snapshot.EVENT:
                if self._stored is not None and self._stored != event["payload"]:
                    # Delay validation until resource loading; metadata must remain available.
                    self._stored = {"conflicting_snapshots": True}
                    break
                self._stored = deepcopy(event["payload"])
            elif event["kind"] in {"codex.thread", "codex.resources.updated"}:
                self._legacy.append(event)

    @property
    def project(self):
        return str(self.backend.journal.root.parents[2])

    def resolve(self):
        if self._stored is not None:
            value = snapshot.validate(self._stored)
        elif self.backend.thread_id:
            try:
                value = legacy_selection(self._legacy)
            except (KeyError, TypeError, ValueError, OSError):
                raise ValueError("旧会话资源选择无法可靠还原，请新建会话；元数据仍可管理") from None
        else:
            return snapshot.document(read_selection(self.backend.provider, self.project)), None, "defaults"
        require_supported(value["selection"]["manifest"])
        return value["selection"], value["fingerprints"], value["origin"]

    def freeze(self, selection, evidence, origin):
        if self._stored is None:
            value = snapshot.create(selection, evidence, origin=origin)
            self.backend._event(snapshot.EVENT, value)
            self._stored = value
            self._legacy = []

    def view(self, selection, drift, origin):
        value = {"state": "frozen" if self._stored is not None else "preview",
                 "origin": origin, "effective": deepcopy(selection), "drift": deepcopy(drift),
                 "pending": None, "changes": []}
        try:
            defaults = read_selection(self.backend.provider, self.project)
            value["defaults"] = {"status": "available", **defaults}
            value["changes"] = snapshot.difference(selection["manifest"], defaults["manifest"])
            value["pending"] = bool(value["changes"])
            value["revision_changed"] = selection["revision"] != defaults["revision"]
        except (ValueError, OSError, RuntimeError):
            value["defaults"] = {"status": "unavailable", "message": "当前默认清单不可读或无效"}
        return value
