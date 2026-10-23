"""Immutable package publication with locked index compare-and-swap."""

import copy
import fcntl
import os
import tempfile
from pathlib import Path

from ..template_schema import TemplateError
from .codec import VERSION, fail, logical, read, relative, sync_directory
from .identity import ROLES
from .shards import Writer, fields, mapping
from .validate import validate


def document(root, name, role, observed, expected=None):
    value, digest = read(relative(root, name), expected)
    observed[name] = digest
    for field in fields(role):
        if field in value:
            value[field] = mapping(root, value[field], name, lambda p, d: observed.update({p: d}))
    return value


def load(root):
    root = Path(root)
    observed = {}
    package = document(root, "package.json", "package", observed)
    if package.get("format") != "sico.circuit-template.package":
        fail("unsupported package/overlay format")
    manifest = package["files"]
    for name, expected in manifest.items():
        if name == "package.json":
            fail("manifest cannot enumerate itself")
        _, actual = read(relative(root, name), expected)
        observed[name] = actual
    consumed = {}
    tables = {
        role: document(root, role + ".json", role, consumed, manifest[role + ".json"])
        for role in ("category", "template", "file", "sources")
    }
    templates = {}
    for key, entry in tables["template"]["items"].items():
        templates[key] = {}
        for role in (*ROLES, "qualification"):
            name = entry["dir"] + "/" + role + ".json"
            if name in manifest:
                templates[key][role] = document(root, name, role, consumed, manifest[name])
    if any(e["detail_level"] != "raw" for e in tables["template"]["items"].values()):
        if set(manifest) != set(consumed):
            fail("projection manifest contains unused or missing content")
    observed.update(consumed)
    if set(observed) - set(manifest) - {p for p in observed if p.startswith("package.")}:
        fail("content shard is missing from manifest")
    validate(package, tables, templates)
    verify(root, observed)
    return dict(package=package, tables=tables, templates=templates, observed=observed, path=root)


def verify(root, observed):
    for name, digest in observed.items():
        read(relative(root, name), digest)


def index(root):
    path = Path(root) / "index.json"
    if not path.exists():
        return dict(format="sico.circuit-template.index", schema_version=VERSION, packages={}), {}
    observed = {}
    value = document(root, "index.json", "index", observed)
    if (
        value.get("format") != "sico.circuit-template.index"
        or value.get("schema_version") != VERSION
    ):
        fail("unsupported index")
    if set(value) != {"format", "schema_version", "packages"}:
        fail("invalid index fields")
    return value, observed


def publish(root, package, tables, templates, *, roots=None, public_only=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    # Deterministic revision includes qualification; retries converge on one package.
    package = copy.deepcopy(package)
    revision = logical(
        dict(
            package={k: v for k, v in package.items() if k not in {"revision", "files"}},
            tables=tables,
            templates=templates,
        )
    ).split(":")[1]
    package["revision"] = revision
    if any(e["detail_level"] != "raw" for e in tables["template"]["items"].values()):
        # Do not even stage private bytes in a public directory on validation failure.
        validate(package, tables, templates)
    destination = relative(root, revision)
    with (root / ".standard.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if public_only:
            from .projection_publish import check_contents

            check_contents(root)
        installed, before = index(root)
        if not destination.exists():
            stage = Path(tempfile.mkdtemp(prefix=".collect-", dir=root))
            writer = Writer(stage)
            for role, value in sorted(tables.items()):
                writer.put(role + ".json", value, role)
            for ident, docs in sorted(templates.items()):
                directory = tables["template"]["items"][ident]["dir"]
                for role, value in sorted(docs.items()):
                    writer.put(directory + "/" + role + ".json", value, role)
            package["files"] = dict(writer.files)
            writer.put("package.json", package, "package", inventory=False)
            load(stage)
            os.rename(stage, destination)
            sync_directory(root)
        try:
            loaded = load(destination)
        except (TemplateError, OSError, KeyError, TypeError, ValueError) as exc:
            if destination.exists():
                fail("immutable package publication conflict: " + str(exc))
            raise
        expected_metadata = {k: v for k, v in package.items() if k != "files"}
        actual_metadata = {k: v for k, v in loaded["package"].items() if k != "files"}
        if (
            loaded["tables"] != tables
            or loaded["templates"] != templates
            or expected_metadata != actual_metadata
        ):
            fail("immutable package publication conflict")
        verify(root, before)
        if index(root)[1] != before:
            fail("index compare-and-swap conflict")
        entry = dict(
            path=revision, roots=dict(private=str(root.resolve())) if roots is None else roots
        )
        installation = "r" + revision
        if installation in installed["packages"] and installed["packages"][installation] != entry:
            fail("index installation conflict")
        installed["packages"][installation] = entry
        # Revision-specific shard prefixes keep pages held by old readers immutable.
        writer = Writer(root)
        packages = installed["packages"]
        from .codec import TARGET, atomic, encode

        if len(encode(packages)) > TARGET:
            installed["packages"] = writer.shard(
                "index." + revision + ".json", "packages", packages, False
            )
        atomic(root / "index.json", installed)
    return destination
