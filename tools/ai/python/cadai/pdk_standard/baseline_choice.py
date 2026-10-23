"""User-confirmed choice between a workspace baseline and SICO_PDK_DATA."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from sicostate import project_directory

from ..pdk_errors import PdkUnavailable
from ..pdk_normalize import now
from . import VERSION
from .jsonio import atomic, fail, mapping, read, relative
from .package import Package, locked
from .provenance import verify
from .publication import require

FORMAT = "sico.pdk.baseline-selection"
DECISIONS = ("sync_shared", "keep_workspace")


def path(workspace):
    return project_directory(workspace, "ai/pdk-baseline-selection.json")


def load(workspace):
    target = path(workspace)
    if not target.is_file():
        return None
    try:
        value = read(target)
    except (OSError, ValueError, PdkUnavailable):
        return None
    if value.get("format") != FORMAT:
        return None
    if value.get("schema_version") not in {"1.0.0", VERSION}:
        return None
    return value


def matches(value, choice):
    if not value or value.get("library") != choice["library"]:
        return False
    local, shared = choice["workspace"][0], choice["environment"][0]
    return (
        value.get("workspace_manifest_digest") == local["manifest_digest"]
        and value.get("environment_manifest_digest") == shared["manifest_digest"]
        and value.get("preference") in {"workspace", "environment"}
    )


def question(choice):
    library = choice["library"]
    local, shared = choice["workspace"][0], choice["environment"][0]
    return {
        "id": "pdk_baseline_sync",
        "header": "PDK 基线同步",
        "question": (
            "工作区 .sico/ai/pdk-data 的 " + library + " 基线（"
            + local["revision"][:12] + "）与 SICO_PDK_DATA（"
            + shared["revision"][:12] + "）不一致。是否将共享基线同步到当前工作区？"
        ),
        "options": [
            {
                "label": "同步共享数据",
                "description": (
                    "将 SICO_PDK_DATA 的基线复制到 .sico/ai/pdk-data，"
                    "并归档当前工作区基线。"
                ),
            },
            {
                "label": "保留工作区数据",
                "description": (
                    "继续使用 .sico/ai/pdk-data 中的本地基线；"
                    "该基线必须符合 SICO-PDK-DATA 标准。"
                ),
            },
        ],
    }


def _write_choice(workspace, choice, preference, evidence):
    local, shared = choice["workspace"][0], choice["environment"][0]
    atomic(path(workspace), {
        "format": FORMAT,
        "schema_version": VERSION,
        "library": choice["library"],
        "preference": preference,
        "workspace_revision": local["revision"],
        "workspace_manifest_digest": local["manifest_digest"],
        "environment_revision": shared["revision"],
        "environment_manifest_digest": shared["manifest_digest"],
        "decided_at": now(),
        "decision_ref": evidence,
    })


def _clear_choice(workspace):
    target = path(workspace)
    if target.exists():
        target.unlink()


def _backup(workspace, library, root, index):
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = project_directory(workspace, "ai/pdk-data.superseded-" + stamp, create=True)
    source = root / "index.json"
    if source.is_file():
        shutil.copy2(source, backup / "index.json")
    moved = []
    for name, entry in list(index["packages"].items()):
        if name != library:
            continue
        package = relative(root, entry["path"])
        if package.exists():
            shutil.move(str(package), str(backup / package.name))
            moved.append(package.name)
        del index["packages"][name]
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name.startswith(library + ".local."):
            shutil.move(str(child), str(backup / child.name))
            moved.append(child.name)
    return backup, moved


def _copy_shared(root, shared):
    destination = root / (shared.manifest["package_id"] + "." + shared.revision[:12])
    if destination.exists():
        existing = Package(destination)
        if existing.manifest_digest != shared.manifest_digest:
            destination = root / (
                shared.manifest["package_id"] + "." + shared.revision[:12]
                + "." + uuid.uuid4().hex[:6]
            )
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".sync-", dir=root))
        try:
            shutil.copytree(shared.root, stage, dirs_exist_ok=True)
            Package(stage).verify()
            os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return destination


def sync_shared(workspace, choice):
    """Copy the shared package into the workspace after archiving the local one."""
    local = choice["workspace"][0]
    shared = choice["environment"][0]
    library = choice["library"]
    root = project_directory(workspace, "ai/pdk-data", create=True)
    shared_package = Package(shared["path"])
    verify(shared_package)
    require(shared_package)
    with locked(root):
        index_path = root / "index.json"
        if index_path.is_file():
            index = read(index_path)
            if index.get("format") != "sico.pdk.index":
                fail("Workspace standard index has unexpected format")
            index["packages"] = mapping(root, index.get("packages", {}))
        else:
            index = {"format": "sico.pdk.index", "schema_version": VERSION, "packages": {}}
        backup, moved = _backup(workspace, library, root, index)
        destination = _copy_shared(root, shared_package)
        index["packages"][library] = {
            "path": destination.name,
            "roots": dict(shared["roots"] or local["roots"]),
        }
        atomic(index_path, index)
    _clear_choice(workspace)
    return {
        "ok": True,
        "status": "synced_shared",
        "library": library,
        "revision": shared_package.revision,
        "path": str(destination / "package.json"),
        "archived": str(backup),
        "archived_entries": moved,
    }


def keep_workspace(workspace, choice, evidence):
    local = choice["workspace"][0]
    package = Package(local["path"])
    verify(package)
    require(package)
    _write_choice(workspace, choice, "workspace", evidence)
    return {
        "ok": True,
        "status": "kept_workspace",
        "library": choice["library"],
        "revision": package.revision,
        "path": str(package.root / "package.json"),
    }


def resolve(workspace, library, environment, args):
    from .workspace import divergence

    choice = divergence(workspace, library, environment)
    if choice is None:
        fail(
            "Workspace and SICO_PDK_DATA baselines already agree",
            "pdk_baseline_choice_not_required",
        )
    if args["baseline_ref"] != choice["baseline_ref"]:
        fail("Baseline evidence changed; read get_pdk_preparation again", "pdk_update_conflict")
    if args.get("choice_confirmed") is not True:
        fail("Baseline choice requires a real user answer", "pdk_choice_confirmation_required")
    if args["decision"] == "sync_shared":
        return sync_shared(workspace, choice)
    if args["decision"] == "keep_workspace":
        return keep_workspace(workspace, choice, uuid.uuid4().hex)
    fail("Unknown baseline decision")


__all__ = [
    "DECISIONS", "FORMAT", "keep_workspace", "load", "matches", "path",
    "question", "resolve", "sync_shared",
]
