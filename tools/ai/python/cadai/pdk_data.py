"""Private PDK metadata database; persisted observations never authorize OA writes."""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

from sicostate import absolute, project_directory

from . import pdk_relocation
from .env_names import value as env_value
from .pdk_builtin import BuiltinPdk
from .pdk_enrich import ENRICHMENT_REVISION
from .pdk_normalize import canonical, context, digest, now
from .pdk_schema import MAX_CACHE_BYTES, SECTIONS, PdkUnavailable

DATA_SCHEMA = "cad.pdk.directory.v1"
DATABASE_SCHEMA = "cad.pdk.database.v2"
MAX_DATABASE_BYTES = 512 * 1024 * 1024
# D4 exclusion reasons recorded instead of silently dropping a library cell.
EXCLUSION_REASONS = frozenset({"model_not_in_decks", "no_symbol_view", "no_cdf_interface",
                               "registry_stale"})
# §2.6 digest scope: observation timestamps never enter the device fingerprint,
# so re-collecting unchanged PDK content keeps the same digest (A5).
VOLATILE_DEVICE_FIELDS = ("captured_at",)


def device_fingerprint(value):
    return digest({key: item for key, item in value.items()
                   if key not in VOLATILE_DEVICE_FIELDS})


def path_stamp(path):
    if not path:
        return None
    try:
        value = Path(path).stat()
        return [value.st_dev, value.st_ino, value.st_mtime_ns, value.st_ctime_ns]
    except OSError:
        return None


def binding(ctx, library):
    lib = next((row for row in ctx["libraries"] if row["name"] == library), None)
    if lib is None or lib.get("status") != "complete" or not lib.get("resolved_path"):
        raise PdkUnavailable("library_unavailable", "Selected library does not resolve")
    return {"library": lib, "collector_revision": ctx["collector_revision"],
            "enrichment_revision": ENRICHMENT_REVISION,
            "virtuoso_version": ctx["virtuoso_version"],
            "path_stamp": path_stamp(lib["resolved_path"]),
            "technology_stamp": path_stamp(lib.get("technology_binding", {}).get("resolved_path"))}


def checked_directory(records, library, view):
    if (not isinstance(records, dict) or records.get("status") != "complete"
            or records.get("truncated") is not False or records.get("issues")
            or not isinstance(records.get("items"), list) or len(records["items"]) > 20000):
        raise PdkUnavailable("incomplete_directory", "PDK directory is incomplete; it cannot be published")
    seen = set()
    for item in records["items"]:
        try:
            target = item["identity"]["target"]
            summary = item["cdf_summary"]
            if (target["library"] != library or target["view"] != view
                    or not isinstance(target["cell"], str) or not target["cell"]
                    or target["cell"] in seen
                    or summary["status"] != "complete"
                    or summary["presence"] not in {"present", "absent", "unknown"}
                    or not isinstance(summary["names"], list)
                    or not all(isinstance(n, str) for n in summary["names"])):
                raise ValueError("invalid directory item")
            seen.add(target["cell"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PdkUnavailable("incomplete_directory", "Invalid or incomplete directory item") from exc
    return records


def read_json(path):
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_CACHE_BYTES + 1)
        if len(raw) > MAX_CACHE_BYTES:
            raise ValueError("data size limit")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (OSError, ValueError) as exc:
        raise PdkUnavailable("invalid_pdk_data", "Cannot read PDK data: " + str(path)) from exc


class PdkData:
    def __init__(self, workspace=None, environment=None):
        self.workspace = absolute(workspace) if workspace else None
        self.environment = os.environ if environment is None else environment
        self._validated_devices = {}
        self.builtin = BuiltinPdk()

    @property
    def root(self):
        return project_directory(self.workspace, "ai/pdk-cache") if self.workspace else None

    def _files(self, root):
        if root.is_file():
            return [root]
        if not root.is_dir():
            raise PdkUnavailable("invalid_pdk_data", "PDK data path is unavailable: " + str(root))
        files, count = [], 0
        for directory, children, names in os.walk(root, followlinks=False):
            # Generated task logs/assets cannot contain published directory data.
            children[:] = sorted(n for n in children if n not in {
                "pdk-acceptance", "pdk-evidence", "pdk-confirmations", "agent", "measurement-results", "circuit_templates", "collections", "devices", "workers", "__pycache__"
            } and not (Path(directory) / n).is_symlink()
                and not (n == "pdk-data" and (Path(directory) / n / "index.json").is_file()))
            count += len(children) + len(names)
            if count > 8192:
                raise PdkUnavailable("pdk_data_limit", "PDK data discovery exceeds its directory budget")
            files.extend(Path(directory) / n for n in sorted(names)
                         if n.endswith(".pdk.json") or n == "pdk-data.json")
            if len(files) > 128:
                raise PdkUnavailable("pdk_data_limit", "Too many PDK data files; specify SICO_PDK_DATA")
        return files

    def discover(self, ctx, view):
        roots, issues = [], []
        found = self.builtin.discover(ctx, view)
        configured = str(env_value(self.environment, "PDK_DATA", "")).strip()
        if configured:
            root = Path(configured).expanduser()
            if not root.is_absolute():
                if self.workspace is None:
                    raise PdkUnavailable("workspace_required", "Relative PDK data requires a workspace")
                root = self.workspace / root
            # Configured metadata is a cache, not an execution policy. Recover
            # missing/stale data from the live process into the private workspace.
            roots.append(("environment", root))
        if self.workspace:
            # Workspace logs/test trees are not metadata search roots. Legacy
            # inputs are supported only at their former dedicated location.
            for root in (self.root, project_directory(self.workspace, "ai/pdk-data")):
                if root.is_dir() and not (root / 'index.json').is_file():
                    roots.append(('workspace', root))
        total = 0
        for source, root in roots:
            try:
                files = self._files(root)
            except PdkUnavailable as exc:
                if exc.code == "pdk_data_limit":
                    raise
                issues.append({"path": str(root), "code": exc.code})
                continue
            for path in files:
                try:
                    value = read_json(path)
                    total += len(canonical(value).encode())
                    if total > 2 * MAX_CACHE_BYTES:
                        raise PdkUnavailable("pdk_data_limit", "PDK data discovery exceeds its byte budget")
                    if value.get("schema") not in {DATA_SCHEMA, DATABASE_SCHEMA} or value.get("digest") != digest(value.get("payload")):
                        raise ValueError("schema/digest mismatch")
                    payload = value["payload"]
                    library = payload["library"]
                    if payload["view"] != view:
                        continue
                    if found.get(library, {}).get("source") == "builtin":
                        continue
                    expected = binding(ctx, library)
                    relocation = None
                    if payload["binding"] != expected:
                        relocation = pdk_relocation.changed_paths(payload["binding"], expected)
                        if relocation is None:
                            raise PdkUnavailable("stale_pdk_data", "PDK library or collector changed")
                    checked_directory(payload["directory"], library, view)
                    complete = value["schema"] == DATABASE_SCHEMA
                    if complete:
                        self.check_devices(payload, path.parent)
                    entry = {"source": source, "path": str(path), "complete": complete, **value}
                    if relocation:
                        entry.update(relocation=relocation, notices=[pdk_relocation.notice(library, relocation)],
                                     payload={**payload, "directory": pdk_relocation.replace_paths(
                                         payload["directory"], relocation["mappings"])})
                    if library in found:
                        previous = found[library]
                        if complete and not previous["complete"]:
                            found[library] = entry
                            continue
                        if previous["complete"] and not complete:
                            continue
                        if previous["source"] == source and previous["digest"] != value["digest"]:
                            raise PdkUnavailable("conflicting_pdk_data", "Conflicting PDK data for " + library)
                        continue
                    found[library] = entry
                except (PdkUnavailable, KeyError, TypeError, ValueError) as exc:
                    if isinstance(exc, PdkUnavailable) and exc.code in {"pdk_data_limit", "conflicting_pdk_data"}:
                        raise
                    issues.append({"path": str(path), "source": source,
                                   "code": getattr(exc, "code", "invalid_pdk_data"),
                                   "recovery": "collect_live_metadata_in_workspace"})
        return found, issues

    def write(self, path, value):
        if path is None:
            return
        raw = canonical(value).encode()
        if len(raw) > MAX_CACHE_BYTES:
            raise PdkUnavailable("pdk_data_limit", "PDK data exceeds its byte budget")
        temporary = None
        try:
            state = project_directory(self.workspace, "ai", create=True)
            if not any(state / name in path.parents for name in ("pdk-cache", "pdk-evidence")):
                raise ValueError("PDK writes must stay in the current project state root")
            relative = path.parent.relative_to(state.parent).as_posix()
            project_directory(self.workspace, relative, create=True)
            with tempfile.NamedTemporaryFile(dir=str(path.parent), delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary), str(path))
        except OSError as exc:
            raise PdkUnavailable("pdk_data_write_failed", "Cannot persist PDK collection data") from exc
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def check_devices(self, payload, root):
        index = payload.get("devices")
        excluded = payload.get("excluded") or {}
        targets = {row["identity"]["target"]["cell"]: row["identity"]["target"]
                   for row in payload["directory"]["items"]}
        if (not isinstance(excluded, dict) or len(excluded) > 20000
                or any(not isinstance(cell, str) or not cell or not isinstance(row, dict)
                       or row.get("reason") not in EXCLUSION_REASONS
                       for cell, row in excluded.items())):
            raise ValueError("Invalid device exclusion report")
        if (not isinstance(index, dict) or set(index) | set(excluded) != set(targets)
                or set(index) & set(excluded)):
            raise PdkUnavailable("incomplete_pdk_data", "Device detail coverage is incomplete")
        total = 0
        for cell, row in index.items():
            fingerprint = row["digest"]
            if (not isinstance(fingerprint, str) or len(fingerprint) != 64
                    or any(c not in "0123456789abcdef" for c in fingerprint)):
                raise ValueError("Invalid device digest")
            path = root / "devices" / (fingerprint + ".json")
            stamp = path_stamp(path)
            cached = self._validated_devices.get(str(path))
            if stamp is None or cached is None or cached[0] != stamp:
                value = read_json(path)
                device = value.get("device")
                if (value.get("schema") != "cad.pdk.device.v1" or value.get("digest") != fingerprint
                        or not isinstance(device, dict) or device_fingerprint(device) != fingerprint
                        or any(not isinstance(device.get(part), dict)
                               or device[part].get("status") not in {"complete", "partial", "unknown", "unavailable"}
                               for part in SECTIONS)):
                    raise PdkUnavailable("incomplete_pdk_data", "Device metadata is missing or changed")
                if stamp != path_stamp(path):
                    raise PdkUnavailable("incomplete_pdk_data", "Device file changed while reading")
                cached = (stamp, device["target"], device["revision"],
                          [part for part in SECTIONS if device[part]["status"] != "complete"],
                          len(canonical(value).encode()))
                if len(self._validated_devices) >= 20000:
                    self._validated_devices.clear()
                self._validated_devices[str(path)] = cached
            if (cached[1] != targets[cell] or row.get("revision") != cached[2]
                    or row.get("incomplete_sections") != cached[3]):
                raise PdkUnavailable("incomplete_pdk_data", "Device metadata is missing or changed")
            total += cached[4]
            if total > MAX_DATABASE_BYTES:
                raise PdkUnavailable("pdk_data_limit", "Device database exceeds 512 MiB")
        if payload.get("coverage") != self.coverage(index):
            raise PdkUnavailable("incomplete_pdk_data", "Device coverage report is inconsistent")

    @staticmethod
    def coverage(devices):
        gaps = {cell: row["incomplete_sections"] for cell, row in devices.items()
                if row["incomplete_sections"]}
        categories, model_status, readiness = {}, {}, {}
        for row in devices.values():
            for name in row.get("categories") or []:
                categories[name] = categories.get(name, 0) + 1
            for counter, value in ((model_status, row.get("model_status") or "unknown"),
                                   (readiness, row.get("readiness") or "unknown")):
                counter[value] = counter.get(value, 0) + 1
        return {"directory_complete": True, "details_complete": True, "device_count": len(devices),
                "metadata_complete": not gaps, "incomplete_device_count": len(gaps),
                "incomplete_devices": dict(list(gaps.items())[:20]),
                "incomplete_devices_truncated": len(gaps) > 20,
                "category_missing": sum(1 for row in devices.values() if not row.get("categories")),
                "categories": dict(sorted(categories.items())),
                "model_status": dict(sorted(model_status.items())),
                "readiness": dict(sorted(readiness.items())),
                "limits_published": sum(1 for row in devices.values() if row.get("limits")),
                "model_decks_published": sum(1 for row in devices.values() if row.get("model_decks"))}

    def checkpoint_valid(self, job):
        try:
            directory = checked_directory(job["capture"]["data"], job["library"], job["view"])
            count = job["detailed_devices"]
            total = job["total_cells"]
            if (job.get("status") not in {"collecting", "capturing", "ready", "failed", "interrupted"}
                    or type(count) is not int or not 0 <= count <= len(directory["items"])
                    or job["phase"] not in {"directory", "details", "complete"}
                    or type(job["next_batch"]) is not int or not 0 <= job["next_batch"] <= 40000
                    or type(job["retry_count"]) is not int or not 0 <= job["retry_count"] <= 2
                    or type(job["scanned_cells"]) is not int
                    or not len(directory["items"]) <= job["scanned_cells"] <= 20000
                    or type(job["device_count"]) is not int or job["device_count"] != len(directory["items"])
                    or (total is not None and (type(total) is not int
                        or not job["scanned_cells"] <= total <= 20000))
                    or (total is None and job["scanned_cells"] != 0)
                    or type(job["elapsed_seconds"]) not in {int, float}
                    or not math.isfinite(job["elapsed_seconds"]) or job["elapsed_seconds"] < 0
                    or not isinstance(job["devices"], dict)
                    or not isinstance(job.get("excluded", {}), dict)
                    or any(not isinstance(cell, str) or not cell
                           for cell in (job.get("excluded") or {}))
                    or any(not isinstance(row, dict) or row.get("enriched") is not True
                           for row in job["devices"].values())):
                return False
            if job["phase"] == "directory":
                if count or job["devices"]:
                    return False
            elif job["total_cells"] != job["scanned_cells"]:
                return False
            tasks = job.get("tasks")
            if tasks is not None:
                cells = {row["identity"]["target"]["cell"] for row in directory["items"]}
                if (not isinstance(tasks, dict) or set(tasks) != cells
                        or any(not isinstance(task, dict)
                               or task.get("state") not in {"pending", "running", "done", "failed"}
                               or type(task.get("attempts")) is not int or not 0 <= task["attempts"] <= 3
                               for task in tasks.values())):
                    return False
                processed = [cell for cell, task in tasks.items() if task["state"] == "done"]
                if len(processed) != count:
                    return False
            else:
                processed = [row["identity"]["target"]["cell"] for row in directory["items"][:count]]
            excluded = {cell: job["excluded"][cell] for cell in job.get("excluded") or {}
                        if cell in set(processed)}
            if len(excluded) != len(job.get("excluded") or {}):
                return False
            if self.root:
                self.check_devices({"directory": {"items": [row for row in directory["items"]
                                       if row["identity"]["target"]["cell"] in set(processed)]},
                                    "devices": job["devices"], "excluded": excluded,
                                    "coverage": self.coverage(job["devices"])}, self.root)
            return True
        except (PdkUnavailable, KeyError, TypeError, ValueError, OverflowError):
            return False

    def device(self, value):
        fingerprint = device_fingerprint(value)
        path = self.root / "devices" / (fingerprint + ".json") if self.root else None
        self.write(path, {"schema": "cad.pdk.device.v1", "digest": fingerprint, "device": value})
        gaps = [part for part in SECTIONS if value[part]["status"] != "complete"]
        return {"digest": fingerprint, "revision": value["revision"], "incomplete_sections": gaps}

    def observation(self, value):
        """Private source evidence for standard generation, including legacy exclusions."""
        fingerprint = device_fingerprint(value)
        if self.root:
            from .pdk_standard.geometry import review_candidates

            self.write(self.root.parent / "pdk-evidence" / (fingerprint + ".json"),
                       {"device": value, "digest": fingerprint,
                        "geometry_review": review_candidates(value)})
        return fingerprint

    def publish_standard(self, published, observations, index):
        if not self.root:
            return {"status": "unavailable", "reason": "workspace_required", "design_ready": False}
        from .pdk_standard.package import publish

        values = {}
        for cell, expected in observations.items():
            record = read_json(self.root.parent / "pdk-evidence" / (expected + ".json"))
            if record.get("digest") != expected or device_fingerprint(record["device"]) != expected:
                raise PdkUnavailable("invalid_pdk_data", "Captured standard evidence changed")
            values[cell] = record["device"]
        from .pdk_standard.lifecycle import reuse
        existing = reuse(self, published["payload"]["library"], values)
        if existing:
            return existing
        return publish(project_directory(self.workspace, "ai/pdk-data", create=True), published["payload"]["library"], published["payload"], values, index)

    def publish(self, capture, library, view, devices=None, excluded=None, quality=None, provenance=None):
        ctx = context(capture["context"])
        payload = {"library": library, "view": view, "binding": binding(ctx, library),
                   "directory": checked_directory(capture["data"], library, view),
                   "captured_at": now()}
        if devices is not None:
            payload["devices"] = devices
            payload["excluded"] = dict(excluded or {})
            if quality is not None:
                payload["quality"] = quality
            if provenance is not None:
                payload["collection"] = provenance
            payload["coverage"] = self.coverage(devices)
            if self.root:
                self.check_devices(payload, self.root)
        value = {"schema": DATABASE_SCHEMA if devices is not None else DATA_SCHEMA,
                 "payload": payload, "digest": digest(payload)}
        path = self.root / (digest([library, view]) + ".pdk.json") if self.root else None
        self.write(path, value)
        return {"source": "workspace" if path else "session", "path": str(path) if path else None, **value}

    def progress(self, job):
        value = job
        if self.root:
            self.write(self.root / "collections" / (job["collection_ref"].split(":")[1] + ".json"), value)
        return value

    def previous(self, library, view):
        if self.root is None or not (self.root / "collections").is_dir():
            return None
        paths = list((self.root / "collections").glob("*.json"))
        if len(paths) > 1024:
            raise PdkUnavailable("collection_limit", "Too many retained PDK collection records")
        matching = []
        for path in paths:
            try:
                value = read_json(path)
            except PdkUnavailable:
                continue
            if value.get("library") == library and value.get("view") == view:
                ref = value.get("collection_ref", "")
                if (isinstance(ref, str) and ref == "pdk_collection:" + path.stem
                        and len(path.stem) == 32 and all(c in "0123456789abcdef" for c in path.stem)
                        and isinstance(value.get("updated_at"), str)):
                    matching.append(value)
        if not matching:
            return None
        value = max(matching, key=lambda row: row.get("updated_at", ""))
        if value.get("status") in {"capturing", "collecting", "ready"}:
            value.update(status="interrupted", automatic_resume_allowed=True)
        return value
