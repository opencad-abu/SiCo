"""File provenance and frozen declarations cannot be supplied by tool arguments."""

import json

import pytest
from wait_reads_fixture import backend, host_question, invoke, project, start

from sicostate import project_root

from sico.core.read_dependencies import MANIFEST
from sico.storage.journal import SessionJournal


@pytest.mark.parametrize("fault", ["missing", "invalid", "unknown", "duplicate", "directory",
                                  "escape", "symlink", "dependencies", "version", "oversize"])
def test_invalid_project_contract_never_enables_waiting_reads(tmp_path, fault):
    project(tmp_path)
    path = project_root(tmp_path) / MANIFEST
    value = json.loads(path.read_text())
    if fault == "missing":
        path.unlink()
    elif fault == "invalid":
        path.write_text('{"version":1,"version":1,"files":[]}')
    elif fault == "oversize":
        path.write_text(" " * 65537)
    else:
        if fault == "unknown":
            value["permissions"] = "allow"
        elif fault == "duplicate":
            value["files"].append(value["files"][0])
        elif fault == "directory":
            value["files"][0]["path"] = "docs"
        elif fault in {"escape", "symlink"}:
            other = tmp_path.parent / (tmp_path.name + "-outside.txt")
            other.write_text("External data")
            if fault == "escape":
                value["files"][0]["path"] = "../" + other.name
            else:
                file = tmp_path / "docs/independent.txt"
                file.unlink()
                file.symlink_to(other)
        elif fault == "dependencies":
            value["files"][0]["depends_on"] = ["unknown"]
        else:
            value["version"] = True
        path.write_text(json.dumps(value))
    with SessionJournal(tmp_path, "contract") as journal:
        loop = backend(journal, tmp_path)
        start(loop)
        host_question(loop)
        try:
            assert invoke(loop, "read_project_document", {"path": "docs/independent.txt"})[
                "status"] == "waiting_user"
        finally:
            loop.runtime = None
            loop.close()


@pytest.mark.parametrize("change", ["manifest", "path", "symlink", "workspace", "argument"])
def test_inflight_contract_or_source_change_cannot_expand_read_access(tmp_path, change):
    project(tmp_path)
    with SessionJournal(tmp_path, "frozen") as journal:
        loop = backend(journal, tmp_path)
        start(loop)
        host_question(loop)
        args = {"path": "docs/independent.txt"}
        try:
            assert invoke(loop, "read_project_document", args)["status"] == "ok"
            if change == "manifest":
                path = project_root(tmp_path) / MANIFEST
                path.write_text(path.read_text() + "\n")
            elif change == "path":
                args["path"] = "docs/undeclared.txt"
            elif change == "symlink":
                path = tmp_path / args["path"]
                path.unlink()
                path.symlink_to(tmp_path / "docs/undeclared.txt")
            elif change == "workspace":
                loop.state.context.snapshot["cwd"] = str(tmp_path.parent)
            else:
                args["depends_on"] = []
            assert invoke(loop, "read_project_document", args)["status"] == "waiting_user"
        finally:
            loop.runtime = None
            loop.close()


def test_no_contract_during_registry_creation_cannot_be_added_by_model(tmp_path):
    with SessionJournal(tmp_path, "absent") as journal:
        loop = backend(journal, tmp_path)
        project(tmp_path)
        start(loop)
        host_question(loop)
        try:
            assert invoke(loop, "read_project_document", {"path": "docs/independent.txt"})[
                "status"] == "waiting_user"
        finally:
            loop.runtime = None
            loop.close()
