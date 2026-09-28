"""Project-declared file dependencies; this contract grants no filesystem access."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..transport.framing import strict_json
from sicostate import project_root

DEPENDENCIES = frozenset({"pdk", "model", "parameters", "target", "decision"})
MANIFEST = "ai/independent-reads.json"
MAX_BYTES = 65536


def reference_dependencies(arguments, context):
    """Fixed API reference handlers consume no project decision or live EDA state."""
    return frozenset()


def target_dependencies(arguments, context):
    """Existing-design evidence reads depend on the captured target only.

    A PDK, model or parameter choice never changes what the current cellview
    contains, so these reads stay independent of every other decision.
    """
    return frozenset({"target"})


def manual_dependencies(arguments, context):
    # Explicit roots can point at project/PDK-specific manuals. Only the existing
    # environment-discovered tool installations have a fixed reference contract.
    return None if "install_root" in arguments else frozenset()


class ProjectReadDependencies:
    """Freeze declarations when the registry is assembled, fail closed on changes.

    Exact files only: directory search could include an undeclared dependent file.
    Declarations supplement existing workspace/argument checks, never replace them.
    No model-call argument can declare itself independent or reload this contract.
    """

    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        try:
            state = project_root(self.workspace, create=False)
        except (OSError, ValueError):
            state = self.workspace / ".sico"
        self.path = state / MANIFEST
        self.digest = ""
        self.files = {}
        try:
            raw = self._read()
            value = strict_json(raw)
            if (not isinstance(value, dict) or set(value) != {"version", "files"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or not isinstance(value["files"], list) or len(value["files"]) > 64):
                raise ValueError("Invalid independent-read contract")
            files = {}
            for row in value["files"]:
                if not isinstance(row, dict) or set(row) != {"path", "depends_on"}:
                    raise ValueError("Invalid independent-read file")
                requested = row["path"]
                if not isinstance(requested, str) or Path(requested).is_absolute():
                    raise ValueError("Expected a project-relative file")
                path = self._file(requested)
                deps = row["depends_on"]
                if (not isinstance(deps, list) or any(not isinstance(d, str) for d in deps)
                        or len(deps) != len(set(deps)) or not set(deps) <= DEPENDENCIES
                        or path in files):
                    raise ValueError("Invalid or duplicate file dependencies")
                files[path] = frozenset(deps)
            self.files, self.digest = files, hashlib.sha256(raw).hexdigest()
        except (ValueError, OSError, RuntimeError, TypeError):
            # A missing/bad declaration must not break ordinary project reads.
            # It simply cannot establish independence while a decision is pending.
            pass

    def _read(self):
        path = self.path.resolve(strict=True)
        path.relative_to(self.workspace)
        if not path.is_file():
            raise ValueError("Expected a regular manifest")
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Read contract exceeds 64 KiB")
        return raw

    def _file(self, value):
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValueError("Missing explicit file path")
        path = (self.workspace / value).resolve(strict=True)
        path.relative_to(self.workspace)
        if not path.is_file() or path == self.path.resolve():
            raise ValueError("Expected a declared regular file")
        return path

    def __call__(self, arguments, context):
        try:
            cwd = context.snapshot.get("cwd")
            if (not self.digest or not isinstance(cwd, str)
                    or Path(cwd).resolve() != self.workspace
                    or hashlib.sha256(self._read()).hexdigest() != self.digest):
                return None
            return self.files.get(self._file(arguments.get("path")))
        except (ValueError, OSError, RuntimeError, TypeError):
            return None
