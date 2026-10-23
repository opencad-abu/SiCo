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
              "model.json": ("configs", "corners", "cards")}
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
    if name == "package.json":
        acceptance = (value.get("publication") or {}).get("acceptance") or {}
        receipts = acceptance.get("receipts")
        if isinstance(receipts, dict) and "$parts" in receipts:
            acceptance["receipts"] = mapping(root, receipts)
        if isinstance(acceptance.get("receipts"), dict):
            for receipt in acceptance["receipts"].values():
                if isinstance(receipt, dict) and isinstance(receipt.get("fingerprints"), dict):
                    for device, fingerprints in receipt["fingerprints"].items():
                        receipt["fingerprints"][device] = mapping(root, fingerprints)
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


def install(root, library, source, roots):
    """Install a complete reviewed package as the workspace baseline for one library.

    The bundled builtin data arrives as an already published generation (its own
    sources, dependencies, acceptance and content digest); installation copies
    those exact bytes and only binds the site roots.  No projection, no edits.
    """
    root, source = Path(root), Path(source)
    package = Package(source)
    package.verify()
    from ..pdk_errors import PdkUnavailable
    from .publication import require
    try:
        require(package)
        design_ready = True
    except PdkUnavailable as exc:
        if exc.code not in {'pdk_acceptance_required', 'pdk_generation_incomplete'}:
            raise
        design_ready = False
    package_id = project.safe_id(library)
    if package.manifest.get("package_id") != package_id:
        fail("Bundled package identity differs from the requested library")
    if (not isinstance(roots, dict) or not roots
            or any(not isinstance(value, str) or not Path(value).is_absolute()
                   for value in roots.values())):
        fail("Install requires absolute site roots")
    revision = package.revision
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
                fail("Confirmed workspace changes require explicit rebase before reinstall",
                     "pdk_update_conflict")
        existing = current["packages"].get(package_id)
        if existing is not None:
            old = Package(relative(root, existing["path"]))
            if old.manifest_digest == package.manifest_digest:
                if existing.get("roots") != roots:
                    existing["roots"] = roots
                    atomic(root / "index.json", current)
                return {"schema_version": VERSION, "package_id": package_id, "revision": revision,
                        "path": str(old.root / "package.json"), "status": "installed",
                        "device_count": len(package.document('device.json')['items']),
                        "design_ready": design_ready, "reused": True}
            if "publication" in old.manifest:
                fail("A different published generation baseline is already installed",
                     "pdk_update_conflict")
            # An unconfirmed capture baseline is replaced by the published bundle;
            # overlays on it were rejected above, so no increment is orphaned.
        stage = Path(tempfile.mkdtemp(prefix=".standard-", dir=root))
        destination = package_id + "." + revision[:12]
        try:
            shutil.copytree(source, stage, dirs_exist_ok=True)
            Package(stage).verify()
            os.rename(stage, root / destination)
            current["packages"][package_id] = {"path": destination, "roots": roots}
            writer = Writer(root)
            packages = current["packages"]
            if len(json.dumps(packages).encode()) > 6000:
                current["packages"] = writer.shard("index." + revision + ".json", "packages", packages)
            atomic(root / "index.json", current)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return {"schema_version": VERSION, "package_id": package_id, "revision": revision,
            "path": str(root / destination / "package.json"), "status": "installed",
            "device_count": len(package.document('device.json')['items']),
            "design_ready": design_ready}


def rebind(root, package_id, expected_digest, roots):
    """Re-point an installed baseline's site roots; the content never changes."""
    root = Path(root)
    if not (root / "index.json").exists():
        return False
    with locked(root):
        current = read(root / "index.json")
        current["packages"] = mapping(root, current["packages"])
        entry = current["packages"].get(package_id)
        if entry is None:
            return False
        installed = Package(relative(root, entry["path"]))
        if installed.manifest_digest != expected_digest:
            fail("Installed baseline differs from the bundled data; explicit rebase required",
                 "pdk_update_conflict")
        if entry.get("roots") == roots:
            return False
        entry["roots"] = roots
        atomic(root / "index.json", current)
        return True


def build(stage, library, revision, payload, values, index):
    writer = Writer(stage)
    at = now()
    source = {"kind": "session", "at": at, "summary": "Observed metadata; design usage has not been confirmed",
              "collector": "SICO-PDK-DATA-" + VERSION + " projection / " + payload["binding"]["collector_revision"],
              "target": {"library": library}}
    from .symbol_facts import METHOD as geometry_method
    sources = {"capture": source, "geometry_derivation": {
        "kind": "derived", "at": at, "summary": "Conservative saved static symbol geometry",
        "inputs": ["capture"], "method": geometry_method}}
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
    files = project.files(index, lib["resolved_path"])
    from .model_collection import METHOD as model_method
    from .model_collection import derive as derive_models

    model_source = "model_deck_derivation"
    model, files, model_dependencies, _ = derive_models(
        files, {"pdk": str(Path(lib["resolved_path"]).parent)}, model_source)
    dependencies.update(model_dependencies)
    if model:
        sources[model_source] = {
            "kind": "derived", "at": at,
            "summary": "Model configurations and corners derived from registered vendor deck sections",
            "inputs": ["capture"], "method": model_method}
        writer.put("model.json", model, ("configs", "corners", "cards"))
    writer.put("sources.json", {"items": sources})
    writer.put("file.json", {**envelope, "items": files}, ("items",))
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
