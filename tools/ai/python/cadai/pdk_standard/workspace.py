"""One effective package selection, including a single checked workspace overlay."""

from sicostate import project_directory
import os
from pathlib import Path
from copy import deepcopy

from ..env_names import value as env_value
from ..pdk_errors import PdkUnavailable
from . import SUPPORTED_VERSIONS
from .jsonio import fail, fingerprint, mapping, read, relative
from .package import Package


def entries(root):
    root = Path(root)
    if root.is_file() and root.name == "package.json":
        package = Package(root)
        return [(root.parent, {"path": ".", "roots": {}}, package.manifest)]
    index = root if root.is_file() else root / "index.json"
    if not index.exists():
        return []
    value = read(index)
    if value.get("format") != "sico.pdk.index":
        return []  # Explicitly legacy; standard callers report unavailable, never bless it.
    if value.get("schema_version") not in SUPPORTED_VERSIONS:
        fail("Unsupported standard index version", "unsupported_pdk_standard")
    result = []
    for entry in mapping(index.parent, value["packages"]).values():
        if not isinstance(entry.get("roots"), dict) or any(not isinstance(v, str) or not Path(v).is_absolute()
                                                         for v in entry["roots"].values()):
            fail("Index resource roots must be absolute directories")
        path = relative(index.parent, entry["path"])
        result.append((path, entry, read(path / "package.json")))
    return result


class Effective:
    def __init__(self, base, roots, overlay=None):
        self.base, self.roots, self.overlay = base, roots, overlay
        self.manifest = deepcopy(base.manifest)
        self.revision = base.revision
        self.changes = {}
        self.dependency_updates = {}
        if overlay:
            from .publication import invalidate
            invalidate(self.manifest)
            manifest = read(overlay / "package.json")
            if manifest.get("format") != "sico.pdk.overlay" or manifest.get("schema_version") not in SUPPORTED_VERSIONS:
                fail("Unsupported overlay version", "unsupported_pdk_standard")
            expected = {"package_id": base.manifest["package_id"], "revision": base.revision,
                        "manifest_digest": base.manifest_digest}
            if manifest.get("base") != expected:
                fail("Workspace overlay base changed", "pdk_update_conflict")
            for name, digest in mapping(overlay, manifest["files"]).items():
                read(relative(overlay, name), digest)
            files = mapping(overlay, manifest["files"])
            if "changes.json" not in files:
                fail("Overlay changes are absent from manifest")
            changes = read(overlay / "changes.json", files["changes.json"])
            self.changes = mapping(overlay, changes["items"])
            self.dependency_updates = mapping(overlay, changes.get('dependency_updates', {}))
            for key, record in self.dependency_updates.items():
                if key in base.manifest['dependencies'] and base.manifest['dependencies'][key] != record:
                    fail('Dependency refresh requires explicit rebase', 'unsupported_pdk_standard')
                self.manifest['dependencies'][key] = record
            from .validate_package import manifest as validate_manifest
            validate_manifest(self.manifest)
            for change in self.changes.values():
                if change.get("replacement_ref") and change["replacement_ref"] not in files:
                    fail("Overlay replacement is absent from manifest")
            self.revision = base.revision + ":" + manifest["revision"]

    def document(self, name):
        change = self.changes.get(name)
        if not change:
            return self.base.document(name)
        from .updates import apply_change
        return apply_change(self, name, change)

    def available(self, name):
        change = self.changes.get(name, {})
        return not change.get("delete_file") and (name in self.base.manifest["files"] or "replacement_ref" in change)


def _candidates(workspace, library, environment):
    environment = os.environ if environment is None else environment
    local = project_directory(workspace, "ai/pdk-data")
    configured = str(env_value(environment, "PDK_DATA", "")).strip()
    installed = []
    if configured:
        path = Path(configured).expanduser()
        installed = entries(path if path.is_absolute() else Path(workspace) / path)
    local_entries = entries(local)
    candidates, overlays = [], []
    for origin, rows in (("environment", installed), ("workspace", local_entries)):
        for path, entry, manifest in rows:
            if manifest.get("format") == "sico.pdk.overlay":
                if origin == "workspace":
                    overlays.append((path, entry, manifest))
                continue
            package = Package(path)
            if any(row["name"] == library for row in package.manifest["libraries"].values()):
                candidates.append((package, entry["roots"], origin))
    if not candidates:
        fail("No standard package; run prepare_pdk_data with refresh=true", "pdk_standard_unavailable")
    # A local publication may supersede only its exact shared baseline.
    replaced = {p.manifest['publication'].get('based_on') for p, _, origin in candidates
                if origin == 'workspace' and 'publication' in p.manifest}
    candidates = [(p, roots, origin) for p, roots, origin in candidates
                  if origin != 'environment' or p.manifest_digest not in replaced]
    unique = {}
    for package, roots, origin in candidates:
        old_roots = unique.get(package.manifest_digest, (None, {}, None))[1]
        unique[package.manifest_digest] = (package, {**old_roots, **roots}, origin)
    candidates = list(unique.values())
    overlays = list({str(row[0].resolve()): row for row in overlays}.values())
    return candidates, overlays


def _divergence(candidates, library):
    """One workspace and one environment baseline disagree; a user choice is needed."""
    workspace = [row for row in candidates if row[2] == "workspace"]
    environment = [row for row in candidates if row[2] == "environment"]
    if len(workspace) != 1 or len(environment) != 1:
        return None
    local, local_roots, _ = workspace[0]
    shared, shared_roots, _ = environment[0]
    if local.manifest_digest == shared.manifest_digest:
        return None
    return {
        "library": library,
        "baseline_ref": fingerprint([library, local.manifest_digest, shared.manifest_digest]),
        "workspace": [{
            "path": str(local.root), "revision": local.revision,
            "manifest_digest": local.manifest_digest, "roots": dict(local_roots),
        }],
        "environment": [{
            "path": str(shared.root), "revision": shared.revision,
            "manifest_digest": shared.manifest_digest, "roots": dict(shared_roots),
        }],
    }


def divergence(workspace, library, environment=None):
    """Describe a workspace-vs-SICO_PDK_DATA choice without selecting either."""
    try:
        candidates, overlays = _candidates(workspace, library, environment)
    except PdkUnavailable:
        return None
    if any(row[2].get("base", {}).get("package_id") == library for row in overlays):
        return None
    return _divergence(candidates, library)


def select(workspace, library, environment=None):
    candidates, overlays = _candidates(workspace, library, environment)
    matching_overlays = [r for r in overlays if any(
        r[2].get("base", {}).get("package_id") == p.manifest["package_id"] for p, _, _ in candidates)]
    if len(matching_overlays) > 1:
        fail("Conflicting workspace overlays", "pdk_update_conflict")
    if matching_overlays:
        path, entry, manifest = matching_overlays[0]
        matching = [(p, roots) for p, roots, _ in candidates if manifest["base"].get("manifest_digest") == p.manifest_digest]
        if len(matching) != 1:
            fail("Overlay requires its exact installed baseline", "pdk_update_conflict")
        base, roots = matching[0]
        return Effective(base, {**roots, **entry.get("roots", {})}, path)
    identities = {p.manifest_digest for p, _, _ in candidates}
    if len(identities) != 1:
        choice = _divergence(candidates, library)
        if choice:
            from . import baseline_choice
            preference = baseline_choice.load(workspace)
            if baseline_choice.matches(preference, choice):
                candidates = [row for row in candidates if row[2] == preference["preference"]]
            else:
                rows = [*choice["workspace"], *choice["environment"]]
                detail = "; ".join(
                    row["path"] + " (revision " + row["revision"] + ")" for row in rows
                )
                fail(
                    "Workspace and SICO_PDK_DATA standard baselines differ: " + detail
                    + "; ask the user whether to sync the shared baseline "
                    "or keep the workspace baseline",
                    "pdk_baseline_sync_required",
                )
        identities = {p.manifest_digest for p, _, _ in candidates}
    if len(identities) != 1:
        rows = sorted(
            {
                (origin, str(package.root), package.revision)
                for package, _, origin in candidates
            }
        )
        shown = rows[:4]
        detail = "; ".join(
            origin + "=" + root + " (revision " + revision + ")"
            for origin, root, revision in shown
        )
        if len(rows) > len(shown):
            detail += "; +" + str(len(rows) - len(shown)) + " more"
        fail("Conflicting standard package baselines: " + detail, "pdk_update_conflict")
    base, roots, _ = candidates[-1]
    return Effective(base, roots)
