"""Atomic cumulative overlay publication shared by reviewed rules and sourced facts.

Internal producers hold the workspace lock and validate revision/source authority.
Dependencies may be added; changing an existing dependency requires explicit rebase.
"""

import os
import shutil
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path

from ..pdk_normalize import now
from . import VERSION, validate
from .jsonio import Writer, atomic, encode, fail, mapping, read, relative
from .package import MAP_FIELDS, SUFFIX_FIELDS


def _retain(writer, root, name, digest):
    """Copy exactly the unchanged document's checked shard closure, not older overlays."""
    if name in writer.files:
        if writer.files[name] != digest:
            fail("Conflicting retained shard")
        return
    value = read(relative(root, name), digest)
    destination = relative(writer.root, name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(relative(root, name), destination)
    writer.files[name] = digest

    def parts(node):
        if isinstance(node, dict):
            if set(node) == {"$parts"}:
                for part in node["$parts"]:
                    _retain(writer, root, part["path"], part["sha256"])
            else:
                for child in node.values():
                    parts(child)
        elif isinstance(node, list):
            for child in node:
                parts(child)

    parts(value)


def publish(root, package, replacements, *, dependencies=None):
    from .updates import expected

    revision = uuid.uuid4().hex
    additions = deepcopy(package.dependency_updates)
    all_dependencies = deepcopy(package.manifest["dependencies"])
    for key, record in (dependencies or {}).items():
        if key in all_dependencies and all_dependencies[key] != record:
            fail("Dependency refresh requires explicit rebase: " + key, "pdk_update_conflict")
        if key not in all_dependencies:
            additions[key] = record
            all_dependencies[key] = record
    stage = Path(tempfile.mkdtemp(prefix=".update-", dir=root))
    try:
        writer = Writer(stage)
        documents = deepcopy(replacements)
        for name, value in documents.items():
            validate.document(name, value)
        changes = deepcopy(package.changes)
        if package.overlay:
            files = mapping(package.overlay, read(package.overlay / "package.json")["files"])
            for name, change in changes.items():
                if name not in documents and "replacement_ref" in change:
                    ref = change["replacement_ref"]
                    _retain(writer, package.overlay, ref, files[ref])
        for i, (name, value) in enumerate(sorted(documents.items())):
            old = package.base.document(name) if name in package.base.manifest["files"] else None
            path = "content_" + revision[:12] + "_" + str(i) + ".json"
            writer.put(
                path,
                value,
                (*MAP_FIELDS.get(name, SUFFIX_FIELDS.get(Path(name).name, ())), "evidence"),
            )
            changes[name] = {
                "expected": expected(old) if old is not None else "absent",
                "dependencies": {
                    dep: all_dependencies[dep]["fingerprint"] for dep in value.get("depends_on", [])
                    if dep in package.base.manifest['dependencies']
                },
                "replacement_ref": path,
            }
        # Added dependencies already carry their one authoritative fingerprint
        # in dependency_updates. Only baseline dependencies need a per-change
        # precondition; repeating a large model inventory here is unbounded.
        for change in changes.values():
            change['dependencies'] = {ref: digest for ref, digest in change['dependencies'].items()
                                      if ref in package.base.manifest['dependencies']}
        writer.put(
            "changes.json",
            {"items": changes, "dependency_updates": additions},
            ("items", "dependency_updates"),
        )
        manifest = {
            "format": "sico.pdk.overlay",
            "schema_version": VERSION,
            "revision": revision,
            "base": {
                "package_id": package.base.manifest["package_id"],
                "revision": package.base.revision,
                "manifest_digest": package.base.manifest_digest,
            },
            "created_at": now(),
            "files": dict(writer.files),
        }
        writer.put("package.json", manifest, ("files",), inventory=False)
        destination = package.base.manifest["package_id"] + ".local." + revision[:12]
        from .workspace import Effective

        candidate = Effective(package.base, package.roots, stage)
        from .provenance import verify

        verify(candidate)
        os.rename(stage, root / destination)
        index = (
            read(root / "index.json")
            if (root / "index.json").exists()
            else {"format": "sico.pdk.index", "schema_version": VERSION, "packages": {}}
        )
        index["schema_version"] = VERSION
        index["packages"] = mapping(root, index["packages"])
        # Keep a local baseline addressable; overlays never chain.
        key = package.base.manifest["package_id"]
        index["packages"][key + ".local"] = {"path": destination, "roots": package.roots}
        if len(encode(index)) > 8000:
            index["packages"] = Writer(root).shard(
                "index." + revision + ".json", "packages", index["packages"]
            )
        atomic(root / "index.json", index)
        return {
            "schema_version": VERSION,
            "status": "ok",
            "revision": package.base.revision + ":" + revision,
            "path": str(root / destination / "package.json"),
            "next_action": "get_pdk_data",
        }
    finally:
        if stage.exists():
            shutil.rmtree(stage)
