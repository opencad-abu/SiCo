"""PDK/project discovery producer. Consumer circuit/spec/write adapters live elsewhere."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .pdk_data import device_fingerprint, read_json
from . import pdk_relocation
from .pdk_bridge import PdkBridge
from .pdk_normalize import context, detail, digest, now, section, summary
from .pdk_schema import SCHEMA, SECTIONS, PdkUnavailable, arguments
from .pdk_store import PdkStore
from .pdk_preparation import PdkPreparation

# D4/A8: an excluded or unpublished cell is reported, never silently collected live.
NOT_INDEXED_ACTIONS = {
    "model_not_in_decks": "report_to_pdk_owner",
    "no_symbol_view": "not_a_front_end_device",
    "no_cdf_interface": "report_to_pdk_owner",
    "registry_stale": "refresh_pdk_collection",
    "not_published": "refresh_pdk_collection",
}


class PdkSession:
    # D7 default view: the design interface projection. The verbatim CDF envelope stays in
    # the workspace database and is returned by tier=all, so the default read is bounded.
    PARAMETER_VIEW_KEYS = ("name", "prompt", "description", "cdf_type", "value_type",
                           "parameter_kind", "default", "units", "choices", "range",
                           "tier", "tier_reason", "default_in_range", "semantic_role")

    def __init__(self, client=None, *, bridge=None, workspace=None, environment=None, entry=None):
        self._legacy_direct = bridge is not None and workspace is None and client is None
        self.bridge = bridge or PdkBridge(client)
        self.store = PdkStore()
        self.preparation = PdkPreparation(self.bridge, workspace=workspace,
                                          environment=environment, entry=entry)
        from .pdk_standard.service import StandardData
        self.standard = StandardData(workspace, environment, self.bridge)
        if self.standard.updates:
            from .pdk_standard.freshness import check_publication
            self.standard.source_validator = lambda package: check_publication(self.bridge, package)
            from .pdk_standard.freshness import check
            self.standard.updates.source_validator = lambda package, diffs: check(self.bridge, package, diffs)

    def call(self, name, args):
        args = arguments(name, args)
        from .pdk_standard.tools import NAMES as STANDARD_NAMES
        if name in STANDARD_NAMES:
            return self.standard.call(name, args)
        return {"get_pdk_preparation": self.preparation.inspect,
                "prepare_pdk_data": self.preparation.prepare,
                "advance_pdk_collection": self.preparation.advance,
                "cancel_pdk_collection": self.preparation.cancel,
                "search_pdk_devices": self.search, "get_pdk_device": self.get_device,
                "revalidate_pdk_bindings": self.revalidate}[name](args)

    def close(self):
        self.preparation.close()

    def retain_binding(self, snapshot_ref, device_ref):
        self.store.retain_device(snapshot_ref, device_ref)

    @staticmethod
    def envelope(ctx):
        # Full library bindings are retained in cache; each item carries its selected library.
        ctx = {k: v for k, v in ctx.items() if k != "libraries"}
        return {"ok": True, "schema_version": SCHEMA, "context": deepcopy(ctx),
                "source_status": "live_session_capture", "cache_policy": "frozen_until_evicted",
                "read_only": True, "callbacks_executed": False}

    def search(self, args):
        query = {k: v for k, v in args.items() if k not in {"cursor", "snapshot_ref"}}
        ref = args.get("snapshot_ref")
        if ref is None:
            if self._legacy_direct:
                capture, source = self.bridge.capture("directory", query), "live_session_capture"
            else:
                capture, source = self.preparation.directory(query)
            if capture is None:
                return source
            ctx = context(capture["context"])
            records = capture["data"]
            libraries = {x["name"]: x for x in ctx["libraries"]}
            library = query.get("library") or self.preparation.selected
            index = None if self._legacy_direct else self._device_index(library)
            index_filters = [key for key in ("category", "tier", "range", "readiness") if query.get(key)]
            if index_filters and index is None:
                raise PdkUnavailable(
                    "device_index_unavailable",
                    "Category/tier/range/readiness filters require a prepared workspace database")
            rows = (index or {}).get("devices") or {}
            excluded = (index or {}).get("excluded") or {}
            items, missing, not_indexed = [], [], []
            for record in records["items"]:
                target = record["identity"]["target"]
                lib = libraries.get(target["library"])
                if lib is None:
                    missing.append(target["library"])
                    continue
                cell = target["cell"]
                if index is not None:
                    if cell in excluded:
                        not_indexed.append({"cell": cell,
                                            "reason": (excluded[cell] or {}).get("reason")})
                        continue
                    if cell not in rows:
                        not_indexed.append({"cell": cell, "reason": "not_published"})
                        continue
                item = summary(record, lib)
                if index is not None:
                    item["device_index"] = self._index_summary(rows.get(cell))
                if (args.get("parameter") or args.get("query")) and record["cdf_summary"]["status"] != "complete":
                    missing.append(item["device_ref"])
                if self._matches(query, item, record, rows.get(cell)):
                    item["library"] = lib
                    items.append(item)
            items.sort(key=lambda x: tuple(x["target"][k] for k in ("library", "cell", "view")))
            ctx["directory_digest"] = digest(records)
            if any(args.get(k) not in (None, "unknown") for k in ("kind", "family")):
                missing.append("classification_provider_unavailable")
            status = "partial" if missing else records["status"]
            ref = self.store.add({"context": ctx, "query": query, "items": items, "details": {},
                                  "directory_source": source,
                                  "directory_library": library,
                                  "device_index": index,
                                  "not_indexed": not_indexed[:64],
                                  "not_indexed_count": len(not_indexed),
                                  "directory": {k: v for k, v in records.items() if k != "items"},
                                  "search_status": status, "unavailable_matches": missing})
        snap = self.store.get(ref)
        if query != snap["query"]:
            raise PdkUnavailable("query_mismatch", "A frozen search snapshot requires identical filters/page size")
        if args.get("snapshot_ref") and not self._legacy_direct:
            selection = self._snapshot_selection(snap)
            if selection is not None:
                return selection
        offset = self.store.offset(ref, query, args.get("cursor"))
        index, requested = snap.get("device_index"), query.get("cell")
        if index is not None and requested and requested not in (index.get("devices") or {}):
            excluded = index.get("excluded") or {}
            reason = (excluded.get(requested) or {}).get("reason") or "not_published"
            return {**self.envelope(snap["context"]), "status": "not_indexed",
                    "library": snap["directory_library"], "cell": requested, "reason": reason,
                    "index_digest": index.get("digest"), "excluded": requested in excluded,
                    "next_action": NOT_INDEXED_ACTIONS.get(reason, "refresh_pdk_collection")}
        result = {**self.envelope(snap["context"]), "status": snap["search_status"],
                  "source_status": ("live_session_capture" if snap["directory_source"] == "live_session_capture"
                                    else "persisted_directory_live_context"),
                  "directory_source": snap["directory_source"],
                  "directory": snap["directory"], "unavailable_matches": snap["unavailable_matches"],
                  "query": query}
        if snap.get("device_index") is not None:
            result["device_index"] = {"digest": snap["device_index"].get("digest"),
                                      "source": snap["device_index"].get("source"),
                                      "coverage": snap["device_index"].get("coverage")}
            result["not_indexed_count"] = snap["not_indexed_count"]
            result["not_indexed"] = snap["not_indexed"]
            result["notices"] = snap["device_index"].get("notices", [])
        return self.store.page(result, snap["items"], ref, query, offset, args["page_size"],
                               assign=lambda r, items: r.update(items=items))

    @staticmethod
    def _matches(query, item, record, row=None):
        if any(query.get(k) and query[k] != item["target"][k] for k in ("library", "cell", "view")):
            return False
        if any(query.get(k) and query[k] != item["classification"][k] for k in ("kind", "family")):
            return False
        names = record["cdf_summary"]["names"]
        if query.get("parameter") and query["parameter"] not in names:
            return False
        if query.get("category"):
            if row is None or query["category"] not in (row.get("categories") or []):
                return False
        if query.get("tier"):
            listed = ((row or {}).get("tiers") or {}).get(query["tier"]) or []
            if row is None or not listed:
                return False
            if query.get("parameter") and query["parameter"] not in listed:
                return False
        if query.get("range"):
            if row is None or query["range"] not in (row.get("ranges") or []):
                return False
        if query.get("readiness"):
            if row is None or row.get("readiness") != query["readiness"]:
                return False
        haystack = " ".join(list(item["target"].values()) + names).casefold()
        return not query.get("query") or query["query"].casefold() in haystack

    @staticmethod
    def _index_summary(row):
        """Bounded per-device summary from the published manifest row (never the detail file)."""
        if not isinstance(row, dict):
            return None
        tiers = row.get("tiers") or {}
        return {"categories": list(row.get("categories") or []),
                "readiness": row.get("readiness"), "model": row.get("model"),
                "model_status": row.get("model_status"),
                "ranges": list(row.get("ranges") or [])[:32],
                "tiers": {name: len(tiers.get(name) or [])
                          for name in ("interface", "derived", "auxiliary")},
                "incomplete_sections": list(row.get("incomplete_sections") or [])}

    def _device_index(self, library):
        """Validated published device index (manifest rows + D4 exclusions) for one library."""
        entry = self.preparation.catalogs.get(library) if library else None
        if not entry or not entry.get("complete"):
            return None
        payload = entry.get("payload") or {}
        if not isinstance(payload.get("devices"), dict) or not isinstance(payload.get("excluded"), dict):
            return None
        path = entry.get("path")
        # Directory exclusions gate coverage; the quality report also carries cells that were
        # considered through the registry (no symbol view / stale registry entries).
        excluded = {cell: dict(row) if isinstance(row, dict) else {"reason": row}
                    for cell, row in payload["excluded"].items()}
        for row in ((payload.get("quality") or {}).get("excluded_devices") or [])[:512]:
            if isinstance(row, dict) and isinstance(row.get("cell"), str):
                excluded.setdefault(row["cell"], {"reason": row.get("reason"),
                                                  "scope": row.get("scope")})
        return {"library": library, "source": entry.get("source"), "digest": entry.get("digest"),
                "root": str(Path(path).parent) if path else None,
                "devices": payload["devices"], "excluded": excluded,
                "relocation": entry.get("relocation"), "notices": entry.get("notices", []),
                "coverage": payload.get("coverage"), "captured_at": payload.get("captured_at")}

    def _freeze_device(self, snap, ref):
        selected = next((x for x in snap["items"] if x["device_ref"] == ref), None)
        if selected is None:
            raise PdkUnavailable("device_unavailable", "Device is not part of this search snapshot")
        capture = self.bridge.capture("device", selected["target"])
        ctx = context(capture["context"])
        changes = self._context_changes(snap["context"], ctx, selected["target"]["library"])
        if changes:
            raise PdkUnavailable("snapshot_changed", "Re-search current session: " + ", ".join(changes))
        value = detail(capture["data"], ctx)
        if value["device_ref"] != ref:
            raise PdkUnavailable("snapshot_changed", "Device binding changed before detail capture")
        snap["details"][ref] = value
        self.store.trim(snap["context"]["snapshot_ref"])
        return value

    def _device_value(self, snap, ref, target):
        """Offline-first device read: the published device file, live capture as fallback."""
        index = snap.get("device_index")
        row = ((index or {}).get("devices") or {}).get(target["cell"]) if (index and target) else None
        if isinstance(row, dict) and index.get("source") == "builtin":
            value = self.preparation.data.builtin.device(snap["context"], target, row["digest"])
            snap["details"][ref] = value
            snap.setdefault("detail_sources", {})[ref] = "builtin_database"
            self.store.trim(snap["context"]["snapshot_ref"])
            return value, "builtin_database"
        if isinstance(row, dict) and index.get("root") and isinstance(row.get("digest"), str):
            path = Path(index["root"]) / "devices" / (row["digest"] + ".json")
            try:
                stored = read_json(path)
            except PdkUnavailable:
                stored = None
            if (isinstance(stored, dict) and stored.get("digest") == row["digest"]
                    and isinstance(stored.get("device"), dict)
                    and device_fingerprint(stored["device"]) == row["digest"]):
                value = stored["device"]
                if index.get("relocation"):
                    lib = next(row for row in snap["context"]["libraries"] if row["name"] == target["library"])
                    value = pdk_relocation.device(value, lib, index["relocation"])
                if value.get("device_ref") == ref:
                    snap["details"][ref] = value
                    snap.setdefault("detail_sources", {})[ref] = "persisted_database"
                    self.store.trim(snap["context"]["snapshot_ref"])
                    return value, "persisted_database"
        value = self._freeze_device(snap, ref)
        snap.setdefault("detail_sources", {})[ref] = "live_session_capture"
        return value, "live_session_capture"

    def get_device(self, args):
        snap = self.store.get(args["snapshot_ref"])
        if not self._legacy_direct:
            selection = self._snapshot_selection(snap)
            if selection is not None:
                return selection
        ref = args["device_ref"]
        target = next((x["target"] for x in snap["items"] if x["device_ref"] == ref), None)
        index = snap.get("device_index")
        excluded = (((index or {}).get("excluded") or {}).get(target["cell"])
                    if target is not None else None)
        if excluded is not None:
            reason = (excluded or {}).get("reason") or "not_published"
            return {**self.envelope(snap["context"]), "status": "not_indexed", "device_ref": ref,
                    "target": target, "reason": reason, "index_digest": index.get("digest"),
                    "next_action": NOT_INDEXED_ACTIONS.get(reason, "refresh_pdk_collection")}
        tier = args.get("tier", "interface")
        query = {"device_ref": ref, "sections": args["sections"], "page_size": args["page_size"],
                 "tier": tier}
        offset = self.store.offset(args["snapshot_ref"], query, args.get("cursor"))
        sources = snap.setdefault("detail_sources", {})
        value = snap["details"].get(ref)
        cache_status = sources.get(ref) if value is not None else None
        if value is None:
            value, cache_status = self._device_value(snap, ref, target)
        result = {**self.envelope(snap["context"]), "cache_status": cache_status,
                  "notices": (index or {}).get("notices", []),
                  "device": {k: deepcopy(v) for k, v in value.items() if k not in SECTIONS}}
        if tier == "interface" and isinstance(result["device"].get("file_layer"), dict):
            # Device document/rule path lists are library-level evidence repeated per device;
            # the default view keeps the digest, counts and a bounded sample (tier=all: full).
            layer = result["device"]["file_layer"]
            documents = layer.get("documents") or []
            layer["documents"] = documents[:4]
            layer["document_count"] = len(documents)
            layer["documents_truncated"] = len(documents) > 4
            rule_decks = layer.get("rule_decks") or []
            layer["rule_decks"] = rule_decks[:2]
            layer["rule_deck_count"] = len(rule_decks)
            layer["projection"] = "interface"
        counts, rows = {}, []
        for part in SECTIONS:
            if part not in args["sections"]:
                result["device"][part] = section()
                counts[part] = 0
                continue
            meta = {k: deepcopy(v) for k, v in value[part].items() if k != "items"}
            items = list(value[part]["items"])
            if tier == "interface" and part in {"parameters", "callbacks"}:
                # The default view is the design interface layer across sections: the CDF
                # callback list otherwise dominates the response (158 rows vs 34).
                collected = [item for item in value["parameters"]["items"]
                             if isinstance(item, dict) and isinstance(item.get("tier"), str)]
                if collected:
                    interface = [item for item in collected if item["tier"] == "interface"]
                    if part == "parameters":
                        items = [{key: deepcopy(item[key]) for key in self.PARAMETER_VIEW_KEYS
                                  if key in item} for item in interface]
                        meta.update(projection="interface",
                                    raw_available="tier=all")
                    else:
                        names = {item.get("name") for item in interface}
                        items = [item for item in items
                                 if "cell_hook" in (item.get("owner") or {})
                                 or (item.get("owner") or {}).get("parameter") in names]
                    meta.update(tier="interface", tier_applied=True, all_count=value[part]["count"])
                elif items and part == "parameters":
                    # Live fallback without collected tiers: keep every parameter and say so.
                    meta.update(tier="interface", tier_applied=False, tier_unavailable=True)
            meta["count"] = len(items)
            result["device"][part] = meta
            counts[part] = len(items)
            rows.extend((part, item) for item in items)

        def assign(response, selected):
            for part in SECTIONS:
                content = [item for key, item in selected if key == part]
                response["device"][part]["items"] = content
                if part in args["sections"]:
                    response["device"][part]["returned"] = len(content)
                    response["device"][part]["truncated"] = len(content) < counts[part]

        return self.store.page(result, rows, args["snapshot_ref"], query, offset,
                               args["page_size"], assign=assign)

    def _snapshot_selection(self, snap):
        capture, selection = self.preparation.directory(snap["query"])
        if capture is None:
            return selection
        library = snap["query"].get("library") or self.preparation.selected
        if library != snap["directory_library"]:
            raise PdkUnavailable("snapshot_changed", "Selected PDK changed; start a new search")
        return None

    @staticmethod
    def _context_changes(old, new, library):
        changes = [k for k in ("session_ref", "session_generation", "project_ref", "virtuoso_version")
                   if old.get(k) != new.get(k)]
        for ctx in (old, new):
            if not any(x["name"] == library for x in ctx["libraries"]):
                changes.append("library_unavailable")
                return changes
        before, after = [next(x for x in ctx["libraries"] if x["name"] == library) for ctx in (old, new)]
        if before != after:
            changes.append("library_binding")
        return changes

    def revalidate(self, args):
        snap = self.store.validation_snapshot(args["snapshot_ref"])
        items = []
        for ref in args["device_refs"]:
            old = snap["details"].get(ref)
            if old is None:
                items.append({"device_ref": ref, "status": "unavailable", "changes": ["detail_required"]})
                continue
            try:
                capture = self.bridge.capture("device", old["target"])
                ctx = context(capture["context"])
                changes = self._context_changes(snap["context"], ctx, old["target"]["library"])
                if "library_unavailable" in changes:
                    raise PdkUnavailable("library_unavailable", "Selected library no longer resolves")
                relocation = (snap.get("device_index") or {}).get("relocation")
                current = (pdk_relocation.observed_device(capture, ctx, relocation) if relocation
                           else detail(capture["data"], ctx))
                changes += [k for k, v in current["dependency_digests"].items()
                            if old["dependency_digests"].get(k) != v]
                unverified = [k for k in SECTIONS if current[k]["status"] != "complete"]
                if (current.get("master_state") or {}).get("modified") is not False:
                    unverified.append("master_saved_state")
                if current["library"]["status"] != "complete":
                    unverified.append("library_resolution")
                version = current["identity"]["items"][0].get("master_file_modified")
                if not version or version.get("type") == "nil" or version.get("status") != "known":
                    unverified.append("master_file_version")
                status = "changed" if changes else "unavailable" if unverified else "valid"
                items.append({"device_ref": ref, "status": status, "changes": changes,
                              "expected_revision": old["revision"], "observed_revision": current["revision"],
                              "expected_dependency_digests": deepcopy(old.get("dependency_digests")),
                              "observed_dependency_digests": dict(current["dependency_digests"]),
                              "checked_at": ctx["captured_at"], "observed_session_generation": ctx["session_generation"],
                              "unverified": unverified,
                              "outside_scope": ["project_model_configuration", "callback_runtime_effects",
                                                "uncollected_master_body", "simulation_readiness"],
                              "checked_dependencies": list(current["dependency_digests"])})
            except PdkUnavailable as exc:
                items.append({"device_ref": ref, "status": "unavailable", "changes": [],
                              "code": exc.code, "message": str(exc)})
        status = "changed" if any(x["status"] == "changed" for x in items) else (
            "unavailable" if any(x["status"] == "unavailable" for x in items) else "valid")
        return {**self.envelope(snap["context"]), "status": status, "items": items,
                "cache_policy": snap.get("cache_policy", "frozen_until_evicted"),
                "checked_at": now(), "cache_used_for_observation": False,
                "observation_source": "live_session_capture",
                "provenance": {"collector_revision": snap["context"].get("collector_revision"),
                               "index_digest": (snap.get("device_index") or {}).get("digest"),
                               "directory_digest": snap["context"].get("directory_digest"),
                               "device_index_source": (snap.get("device_index") or {}).get("source")},
                "validation_scope": "collected_effective_cell_and_symbol_metadata",
                "atomic_with_future_write": False}
