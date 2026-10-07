"""Atomic standard package publication and content-checked logical reads."""

import json
import fcntl
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..pdk_normalize import now
from . import VERSION
from . import project, validate, geometry
from .jsonio import Writer, atomic, fail, mapping, read, relative, sha

MAP_FIELDS = {"package.json": ("libraries", "dependencies", "files"),
              "sources.json": ("items",), "category.json": ("items",),
              "device.json": ("items",), "file.json": ("items",),
              "model.json": ("configs", "corners")}
SUFFIX_FIELDS = {"cdf.json": ("parameters", "rules"), "symbol.json": ("terminals",),
                 "simulation.json": ("interfaces",), "properties.json": ("items",), "iv.json": ("runs",)}


@contextmanager
def locked(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".standard.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def logical(root, name, expected=None, *, logical_name=None):
    value = read(relative(root, name), expected)
    name = logical_name or name
    for field in (*MAP_FIELDS.get(name, SUFFIX_FIELDS.get(Path(name).name, ())), "evidence"):
        if field in value:
            value[field] = mapping(root, value[field])
    return value


class Package:
    def __init__(self, path):
        self.root = Path(path).parent if Path(path).is_file() else Path(path)
        self.manifest = logical(self.root, "package.json")
        self.manifest_digest = sha((self.root / "package.json").read_bytes())
        validate.document("package.json", self.manifest)
        self.revision = self.manifest["revision"]

    def document(self, name):
        expected = self.manifest["files"].get(name)
        if not expected:
            fail("Content unavailable: " + name, "pdk_content_unavailable")
        value = logical(self.root, name, expected)
        validate.document(name, value)
        return value

    def available(self, name):
        return name in self.manifest["files"]

    def verify(self):
        for name, expected in self.manifest["files"].items():
            read(relative(self.root, name), expected)
        sources = self.document("sources.json")["items"]
        devices = self.document("device.json")["items"]
        categories = self.document("category.json")["items"]
        for name in ("device.json", "category.json", "file.json"):
            value = self.document(name)
            self.references(value, sources)
        for row in devices.values():
            if row["library"] not in self.manifest["libraries"]:
                fail("Device library is not in manifest")
            if isinstance(row["categories"], list) and set(row["categories"]) - set(categories):
                fail("Unknown category reference")
            if "dir" in row:
                name = row["dir"] + "/cdf.json"
                self.references(self.document(name), sources)
        from .provenance import verify
        return verify(self)

    def references(self, value, sources):
        refs = {value["source"], *value.get("evidence", {}).values()}
        if refs - set(sources):
            fail("Unknown provenance reference")
        if set(value["depends_on"]) - set(self.manifest["dependencies"]):
            fail("Unknown source dependency")


def publish(root, library, payload, values, index):
    """Publish only unconfirmed facts; legacy evidence remains a separate implementation detail."""
    root = Path(root)
    package_id = project.safe_id(library)
    revision = uuid.uuid4().hex
    with locked(root):
        current = read(root / "index.json") if (root / "index.json").exists() else {
            "format": "sico.pdk.index", "schema_version": VERSION, "packages": {}}
        if current.get("format") != "sico.pdk.index":
            fail("Workspace standard index has unexpected format")
        current["packages"] = mapping(root, current["packages"])
        for entry in current["packages"].values():
            installed = relative(root, entry["path"])
            old = read(installed / "package.json")
            if old.get("format") == "sico.pdk.overlay" and old.get("base", {}).get("package_id") == package_id:
                fail("Confirmed workspace changes require explicit rebase before recollection",
                     "pdk_update_conflict")
        stage = Path(tempfile.mkdtemp(prefix=".standard-", dir=root))
        try:
            build(stage, library, revision, payload, values, index)
            Package(stage).verify()
            destination = package_id + "." + revision[:12]
            os.rename(stage, root / destination)
            libpath = payload["binding"]["library"]["resolved_path"]
            current["packages"][package_id] = {"path": destination, "roots": {"pdk": str(Path(libpath).parent)}}
            # Index shards use revision-specific names so old readers retain immutable files.
            writer = Writer(root)
            packages = current["packages"]
            if len(json.dumps(packages).encode()) > 6000:
                current["packages"] = writer.shard("index." + revision + ".json", "packages", packages)
            atomic(root / "index.json", current)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return {"schema_version": VERSION, "package_id": package_id, "revision": revision,
            "path": str(root / destination / "package.json"), "status": "incomplete",
            "device_count": len(payload["directory"]["items"]), "design_ready": False,
            "readiness_scope": "metadata_capture_only",
            "next_action": "complete_pdk_generation", "confirmation_required": True}


def build(stage, library, revision, payload, values, index):
    writer = Writer(stage)
    at = now()
    source = {"kind": "session", "at": at, "summary": "Observed metadata; design usage has not been confirmed",
              "collector": "SICO-PDK-DATA-" + VERSION + " projection / " + payload["binding"]["collector_revision"],
              "target": {"library": library}}
    from .symbol_facts import METHOD as geometry_method
    writer.put("sources.json", {"items": {"capture": source, "geometry_derivation": {
        "kind": "derived", "at": at, "summary": "Conservative saved static symbol geometry",
        "inputs": ["capture"], "method": geometry_method}}})
    envelope = {"source": "capture", "depends_on": []}
    rows, categories, dependencies = {}, {}, {}
    targets = {r["identity"]["target"]["cell"]: r["identity"]["target"] for r in payload["directory"]["items"]}
    for cell, target in targets.items():
        key = project.safe_id(cell)
        value = values.get(cell)
        cats = (value or {}).get("categories", {}).get("all", [])
        categories.update({project.safe_id(c): {"name": c} for c in cats})
        row = project.device(target, cats)
        if value:
            row["dir"] = key
            deps = project.dependencies(value, key)
            # Retained legacy/builtin captures may lack body observations. Do not
            # claim v3 coverage until the current collector has read the body.
            method = (geometry.METHOD if value['geometry'].get('body_outline_status') == 'complete'
                      else 'sico-symbol-interface-json-v1')
            deps[key + "_symbol"] = {"kind": "symbol", "target": {**target, "library": project.safe_id(library)},
                "fingerprint": geometry.signature(value, method), "method": method}
            dependencies.update(deps)
            writer.put(key + "/cdf.json", project.cdf(value, key + "_cdf"), ("parameters",))
            writer.put(key + "/symbol.json", geometry.project(value, key + "_symbol"), ("terminals", "evidence"))
        rows[key] = row
    # Registry-only/no-symbol cells remain observable, never inferred to be permitted.
    for entry in (payload.get("quality") or {}).get("excluded_devices", []):
        key = project.safe_id(entry["cell"])
        if key not in rows:
            row = project.device({"library": library, "cell": entry["cell"], "view": "symbol"})
            row["view"] = project.unknown(entry["reason"])
            row["reason"] = entry["reason"]
            rows[key] = row
    writer.put("category.json", {**envelope, "items": categories}, ("items",))
    writer.put("device.json", {**envelope, "items": rows}, ("items",))
    lib = payload["binding"]["library"]
    writer.put("file.json", {**envelope, "items": project.files(index, lib["resolved_path"])}, ("items",))
    manifest = {"format": "sico.pdk.package", "schema_version": VERSION,
                "package_id": project.safe_id(library),
                "pdk_version": lib.get("pdk_version") or project.unknown("PDK release identity not supplied by session"),
                "options": {"configuration": project.unknown("Process options require confirmation")},
                "revision": revision, "created_at": at,
                "libraries": {project.safe_id(library): {"name": library, "root": "pdk",
                                                         "path": Path(lib["resolved_path"]).name}},
                "dependencies": dependencies}
    if len(json.dumps(dependencies).encode()) > 6000:
        manifest["dependencies"] = writer.shard("package.json", "dependencies", dependencies)
    inventory = dict(writer.files)
    manifest["files"] = writer.shard("package.json", "files", inventory, inventory=False) if len(
        json.dumps(inventory).encode()) > 6000 else inventory
    writer.put("package.json", manifest, inventory=False)
