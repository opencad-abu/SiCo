"""Cumulative workspace overlays over one fixed base package (appendix C.3/C.4 core).

An overlay names its base by the fixed triple ``(package_id, revision,
manifest_digest)`` and carries logical-file changes with expected digests and
dependency preconditions.  This module owns the document model, change validation
(RFC 6902 add/replace/remove subset over object members only), the deterministic
merge, the publisher and index registration; the reader and derived index also
serve registered overlays.

Development status (2026-10-11): paused by user decision.  Builtin template
updates use the direct reload path (docs/SICO_BUILTIN_TEMPLATE_RELOAD.md); the
re-validation, rebase and committer slices are not being built.  Do not extend
this module without explicit user direction.
"""

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .codec import VERSION, encode, fail, logical, read, relative, sha, sync_directory
from .identity import ROLES, reference
from .package import document, load

FORMAT = "sico.circuit-template.overlay"
OPS = {"add", "replace", "remove"}
TABLES = ("category", "template", "file", "sources")
CONTENT = (*ROLES, "qualification")
UPDATES = {
    "dependency_updates": "dependencies",
    "pdk_updates": "pdks",
    "rule_updates": "rules",
}
REVALIDATION_REASON = "Overlay change requires re-validation"
CHANGES = "changes.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def _keys(row, required, optional=""):
    from .validate import keys

    keys(row, required, optional)


def _digest(value):
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:")


def _jcs_digest(value):
    return isinstance(value, str) and len(value) == 75 and value.startswith("sha256-jcs:")


def _logical_path(name):
    if not isinstance(name, str) or not name.endswith(".json") or "\\" in name:
        fail("invalid logical path: " + str(name))
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in {"", ".", ".."} for p in name.split("/")):
        fail("logical path escapes the package: " + name)
    if name == "package.json":
        fail("overlay changes cannot target package.json")
    return name


def _role(name):
    stem = name.rsplit("/", 1)[-1][: -len(".json")]
    return stem if stem in (*CONTENT, *TABLES) else None


def read_overlay(root):
    """Read and validate one overlay directory; returns package/changes/replacements."""
    root = Path(root)
    observed = {}
    package, _ = read(root / "package.json")
    observed["package.json"] = sha((root / "package.json").read_bytes())
    _keys(package, "format schema_version revision base files created_at")
    if package["format"] != FORMAT or package["schema_version"] != VERSION:
        fail("unsupported package/overlay format")
    if not isinstance(package["revision"], str) or not package["revision"]:
        fail("invalid overlay revision")
    base = package["base"]
    _keys(base, "package_id revision manifest_digest")
    for key in ("package_id", "revision"):
        if not isinstance(base[key], str) or not base[key]:
            fail("invalid overlay base " + key)
    if not _digest(base["manifest_digest"]):
        fail("invalid overlay base digest")
    files = package["files"]
    if not isinstance(files, dict) or CHANGES not in files:
        fail("overlay manifest must list changes.json")
    if "package.json" in files:
        fail("overlay manifest cannot enumerate itself")
    for name, digest in files.items():
        _logical_path(name)
        if not _digest(digest):
            fail("invalid overlay manifest digest for " + name)
    values = {}
    for name in sorted(files):
        role = _role(name)
        if role:
            values[name] = document(root, name, role, observed, files[name])
        else:
            values[name], _ = read(relative(root, name), files[name])
            observed[name] = sha(relative(root, name).read_bytes())
    if set(observed) - {"package.json"} != set(files):
        fail("overlay manifest does not cover exactly its files")
    changes = values[CHANGES]
    _check_changes(changes)
    return dict(
        path=root,
        package=package,
        changes=changes,
        replacements={name: value for name, value in values.items() if name != CHANGES},
        observed=observed,
    )


def _check_changes(changes):
    _keys(changes, "items", " ".join(UPDATES))
    if not isinstance(changes["items"], dict):
        fail("overlay items must be a mapping")
    for path, record in changes["items"].items():
        _check_record(_logical_path(path), record)
    for key, table in UPDATES.items():
        rows = changes.get(key, {})
        if not isinstance(rows, dict) or any(not isinstance(v, dict) for v in rows.values()):
            fail("overlay " + key + " must map IDs to full rows")


def _check_record(path, record):
    _keys(record, "expected dependencies", "ops replacement_ref delete_file")
    expected = record["expected"]
    if expected != "absent" and not _jcs_digest(expected):
        fail("invalid overlay expected digest: " + path)
    if not isinstance(record["dependencies"], dict):
        fail("overlay dependencies must be a mapping")
    modes = [key for key in ("ops", "replacement_ref", "delete_file") if key in record]
    if len(modes) != 1:
        fail("overlay change needs exactly one of ops/replacement_ref/delete_file: " + path)
    mode = modes[0]
    if mode == "ops":
        if expected == "absent":
            fail("new logical files must use replacement_ref: " + path)
        if not isinstance(record["ops"], list) or not record["ops"]:
            fail("overlay ops must be a non-empty list")
        for op in record["ops"]:
            _check_op(op)
        if path == "template.json":
            for op in record["ops"]:
                if op["path"].startswith("/items/") and op["path"].split("/")[-1] in {
                    "template_ref",
                    "dir",
                }:
                    fail("template_ref and dir are derived, not editable: " + op["path"])
    elif mode == "replacement_ref":
        if not isinstance(record["replacement_ref"], str) or not record["replacement_ref"]:
            fail("invalid replacement_ref: " + path)
        _logical_path(record["replacement_ref"])
        if record["replacement_ref"] == CHANGES:
            fail("changes.json cannot be replaced")
    else:
        if record["delete_file"] is not True:
            fail("delete_file must be true: " + path)
        if path.endswith("/" + "qualification.json") or path == "qualification.json":
            fail("qualification is re-issued by the committer, not deleted")
    if path.endswith("/" + "qualification.json") or path == "qualification.json":
        fail("qualification is re-issued by the committer, not edited")


def _check_op(op):
    _keys(op, "op path", "value")
    if op["op"] not in OPS:
        fail("unsupported overlay op: " + str(op["op"]))
    _tokens(op["path"])
    if op["op"] in {"add", "replace"} and "value" not in op:
        fail("overlay op requires a value")
    if op["op"] == "remove" and "value" in op:
        fail("overlay remove takes no value")


def _tokens(pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        fail("overlay op must address an object member: " + str(pointer))
    tokens = []
    for token in pointer[1:].split("/"):
        if token == "" or "/" in token.replace("~1", ""):
            fail("invalid pointer token: " + pointer)
        token = token.replace("~1", "/").replace("~0", "~")
        tokens.append(token)
    if not tokens:
        fail("overlay op must address an object member: " + str(pointer))
    return tokens


def _apply(files, path, op):
    if path not in files or not isinstance(files[path], dict):
        fail("overlay ops require an existing logical file: " + path)
    tokens = _tokens(op["path"])
    node = files[path]
    for token in tokens[:-1]:
        if not isinstance(node, dict) or token not in node:
            fail("pointer does not address an object member: " + op["path"])
        node = node[token]
    key = tokens[-1]
    if not isinstance(node, dict):
        fail("pointer does not address an object member: " + op["path"])
    if op["op"] == "add":
        if key in node:
            fail("add targets an existing member: " + op["path"])
        node[key] = copy.deepcopy(op["value"])
    elif op["op"] == "replace":
        if key not in node:
            fail("replace targets a missing member: " + op["path"])
        node[key] = copy.deepcopy(op["value"])
    else:
        if key not in node:
            fail("remove targets a missing member: " + op["path"])
        del node[key]


def logical_files(package):
    """Every logical document of a loaded package except package.json."""
    files = {role + ".json": copy.deepcopy(package["tables"][role]) for role in TABLES}
    for ident, docs in package["templates"].items():
        directory = package["tables"]["template"]["items"][ident]["dir"]
        for role, value in docs.items():
            files[directory + "/" + role + ".json"] = copy.deepcopy(value)
    return files


def _check_dependencies(record, package, updates, doc):
    listed = record["dependencies"]
    needed = set(doc.get("depends_on", [])) if isinstance(doc, dict) else set()
    missing = needed - set(listed)
    if missing:
        fail("overlay change drops declared dependencies: " + ", ".join(sorted(missing)))
    refresh = updates.get("dependency_updates", {})
    for key, fingerprint in listed.items():
        if key not in package["dependencies"]:
            fail("overlay dependency is not registered: " + key)
        authoritative = (
            refresh[key]["fingerprint"]
            if key in refresh
            else package["dependencies"][key]["fingerprint"]
        )
        if fingerprint != authoritative:
            fail("overlay dependency fingerprint is stale; refresh it in dependency_updates: " + key)


def merge(base_root, overlay_root):
    """Validate one overlay against its fixed base and return the effective view."""
    base_root = Path(base_root)
    base = load(base_root)
    ov = read_overlay(overlay_root)
    base_bytes = (base_root / "package.json").read_bytes()
    expected_base = ov["package"]["base"]
    if (
        base["package"]["package_id"] != expected_base["package_id"]
        or base["package"]["revision"] != expected_base["revision"]
        or sha(base_bytes) != expected_base["manifest_digest"]
    ):
        fail("overlay base does not match the installed base package")
    package = {
        key: copy.deepcopy(value)
        for key, value in base["package"].items()
        if key not in {"files", "revision"}
    }
    package["files"] = {}
    for key, table in UPDATES.items():
        for ident, row in sorted(ov["changes"].get(key, {}).items()):
            package[table][ident] = copy.deepcopy(row)
    files = logical_files(base)
    for path, record in sorted(ov["changes"]["items"].items()):
        before = files.get(path)
        have = "absent" if path not in files else logical(before)
        if record["expected"] != have:
            fail("overlay expected digest does not match the base: " + path)
        if "replacement_ref" in record:
            replacement = ov["replacements"].get(record["replacement_ref"])
            if replacement is None:
                fail("replacement_ref is not part of the overlay: " + record["replacement_ref"])
            if record["expected"] != "absent":
                _check_dependencies(record, package, ov["changes"], replacement)
            files[path] = copy.deepcopy(replacement)
        elif record.get("delete_file"):
            _check_dependencies(record, package, ov["changes"], before)
            del files[path]
        else:
            _check_dependencies(record, package, ov["changes"], before)
            for op in record["ops"]:
                _apply(files, path, op)
    tables = {role: files[role + ".json"] for role in TABLES}
    templates = {}
    for ident, entry in tables["template"]["items"].items():
        directory = entry["dir"]
        docs = {
            role: files[directory + "/" + role + ".json"]
            for role in CONTENT
            if directory + "/" + role + ".json" in files
        }
        templates[ident] = docs
    changed = []
    for ident in sorted(templates):
        expected_ref = reference(package, tables, templates[ident], ident)
        if expected_ref == base["tables"]["template"]["items"][ident]["template_ref"]:
            continue
        changed.append(ident)
        tables["template"]["items"][ident]["template_ref"] = expected_ref
        qualification = templates[ident].get("qualification")
        if qualification:
            qualification["template_ref"] = expected_ref
            for profile, row in qualification["checks"].items():
                qualification["checks"][profile] = dict(
                    result="unknown", reason=REVALIDATION_REASON
                )
    from .validate import validate

    package["revision"] = ov["package"]["revision"]
    validate(package, tables, templates)
    return dict(
        package=package,
        tables=tables,
        templates=templates,
        changed=changed,
        base=ov["package"]["base"],
        overlay_revision=ov["package"]["revision"],
    )


def resolve(base, candidates):
    """The unique installed package directory matching one base triple."""
    hits = []
    for root in candidates:
        path = Path(root) / "package.json"
        if not path.exists():
            continue
        try:
            value, _ = read(path)
        except Exception:
            continue
        if (
            value.get("package_id") == base["package_id"]
            and value.get("revision") == base["revision"]
            and sha(path.read_bytes()) == base["manifest_digest"]
        ):
            hits.append(Path(root))
    if not hits:
        fail("overlay base is not installed: " + str(base["package_id"]))
    if len(hits) > 1:
        fail("overlay base is ambiguous: " + ", ".join(str(hit) for hit in hits))
    return hits[0]


def effective_revision(base_bytes_digest, overlay_bytes_digest):
    return logical(dict(base=base_bytes_digest, overlay=overlay_bytes_digest))


def _revision(base, changes, replacements):
    return hashlib.sha256(
        encode(dict(base=base, changes=changes, replacements=replacements))
    ).hexdigest()


def _register(root, name, revision, roots):
    import fcntl

    from .codec import atomic
    from .package import index as read_index
    from .package import verify

    root = Path(root)
    if not (root / "index.json").exists():
        atomic(
            root / "index.json",
            dict(format="sico.circuit-template.index", schema_version=VERSION, packages={}),
        )
    with (root / ".standard.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        installed, before = read_index(root)
        verify(root, before)
        if read_index(root)[1] != before:
            fail("index compare-and-swap conflict")
        entry = dict(path=name, roots=dict(roots))
        installation = "o" + revision
        previous = installed["packages"].get(installation)
        if previous is not None and previous != entry:
            fail("overlay installation conflict: " + installation)
        installed["packages"][installation] = entry
        atomic(root / "index.json", installed)


def publish(destination, base_root, changes, replacements=None, *, at=None, register=False,
            roots=None):
    """Write one standalone overlay directory; deterministic and re-runnable."""
    from .validate import keys

    base_root = Path(base_root)
    destination = Path(destination)
    base_package, _ = read(base_root / "package.json")
    base = dict(
        package_id=base_package["package_id"],
        revision=base_package["revision"],
        manifest_digest=sha((base_root / "package.json").read_bytes()),
    )
    replacements = {name: value for name, value in (replacements or {}).items()}
    _check_changes(changes)
    for name in replacements:
        _logical_path(name)
    revision = _revision(base, changes, replacements)
    if destination.exists():
        existing, _ = read(destination / "package.json")
        if existing.get("revision") == revision:
            if register:
                _register(destination.parent, destination.name, revision, roots or {})
            return existing
        fail("overlay destination already holds a different revision: " + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    import tempfile

    stage = Path(tempfile.mkdtemp(prefix=".overlay-", dir=destination.parent))
    try:
        files = {}
        for name in sorted([CHANGES, *replacements]):
            raw = encode(changes if name == CHANGES else replacements[name])
            path = relative(stage, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            files[name] = sha(raw)
        package = dict(
            format=FORMAT,
            schema_version=VERSION,
            revision=revision,
            base=base,
            files=files,
            created_at=at or now(),
        )
        keys(package, "format schema_version revision base files created_at")
        (stage / "package.json").write_bytes(encode(package))
        merge(base_root, stage)
        sync_directory(stage)
        destination.mkdir(parents=True, exist_ok=True)
        for path in stage.rglob("*"):
            if path.is_file():
                target = relative(destination, str(path.relative_to(stage)))
                target.parent.mkdir(parents=True, exist_ok=True)
                path.rename(target)
        sync_directory(destination)
        if register:
            _register(destination.parent, destination.name, revision, roots or {})
        return package
    finally:
        import shutil

        if stage.exists():
            shutil.rmtree(stage)
