"""One-label-per-session navigation layout and its catalog projection."""

import json
import threading
from types import SimpleNamespace

import pytest
from sico.core.tools import ToolRegistry
from sico.service.catalog import CatalogService
from sico.service.frontend_hub import FrontendHub
from sico.service.navigation_label_tools import attach_label_tools
from sico.service.session_labels import SessionLabels
from sico.storage import navigation_labels as storage


def test_label_store_is_project_owned_revisioned_and_idempotent(tmp_path):
    labels = SessionLabels(tmp_path)
    assert labels.snapshot() == {
        "revision": None, "labels": [], "loose_order": [], "error": "",
    }
    first = labels.create("PDK", expected_revision=None)
    assert first["labels"][0]["name"] == "PDK"
    assert first["revision"]
    again = labels.create("pdk", expected_revision=None)
    assert again["revision"] == first["revision"]

    label_id = first["labels"][0]["id"]
    renamed = labels.rename(label_id, "Design", expected_revision=first["revision"])
    assert renamed["labels"][0]["name"] == "Design"
    with pytest.raises(ValueError, match="已被修改"):
        labels.rename(label_id, "Stale", expected_revision=first["revision"])


def test_moving_a_session_removes_its_old_single_label(tmp_path):
    labels = SessionLabels(tmp_path)
    first = labels.create("PDK", expected_revision=None)
    a = first["labels"][0]["id"]
    second = labels.create("Simulation", expected_revision=first["revision"])
    b = second["labels"][1]["id"]
    moved = labels.move_sessions(["s1", "s2"], a, None,
                                 expected_revision=second["revision"])
    moved = labels.move_sessions(["s2", "s3"], b, None,
                                 expected_revision=moved["revision"])
    by_id = {row["id"]: row for row in moved["labels"]}
    assert by_id[a]["sessions"] == ["s1"]
    assert by_id[b]["sessions"] == ["s2", "s3"]
    assert moved["loose_order"] == []

    reordered = labels.move_sessions(["s3"], b, "s2",
                                     expected_revision=moved["revision"])
    assert reordered["labels"][1]["sessions"] == ["s3", "s2"]
    removed = labels.move_sessions(["s2"], None, None,
                                   expected_revision=reordered["revision"])
    assert removed["labels"][1]["sessions"] == ["s3"]
    assert removed["loose_order"] == ["s2"]


def test_deleting_a_label_moves_its_sessions_to_unclassified(tmp_path):
    labels = SessionLabels(tmp_path)
    created = labels.create("PDK", expected_revision=None)
    label_id = created["labels"][0]["id"]
    moved = labels.move_sessions(["s1", "s2"], label_id, None,
                                 expected_revision=created["revision"])
    deleted = labels.delete(label_id, expected_revision=moved["revision"])
    assert deleted["labels"] == []
    assert deleted["loose_order"] == ["s1", "s2"]
    assert SessionLabels(tmp_path).snapshot()["loose_order"] == ["s1", "s2"]


def test_invalid_one_label_document_is_reported_without_being_repaired(tmp_path):
    path = storage.default_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema": storage.SCHEMA,
        "labels": [
            {"id": "l_" + "a" * 32, "name": "A", "sessions": ["s1"]},
            {"id": "l_" + "b" * 32, "name": "B", "sessions": ["s1"]},
        ],
        "loose_order": [],
    }), encoding="utf-8")
    result = SessionLabels(tmp_path).snapshot()
    assert result["error"]
    assert result["labels"] == []


def test_catalog_publishes_referenced_label_rows_beyond_the_main_limit():
    available = {
        str(index): {"id": str(index), "modified": index, "name": "s" + str(index)}
        for index in range(205)
    }
    state = {
        "revision": "r",
        "labels": ({"id": "l_" + "a" * 32, "name": "Old",
                    "sessions": ("0", "1")},),
        "loose_order": ("2",),
        "error": "",
    }
    value = CatalogService._value(available, [], labels=lambda: state)
    assert len(value["rows"]) == 200
    assert {row["id"] for row in value["label_rows"]} == {"0", "1", "2"}
    assert value["labels"]["revision"] == "r"


def test_label_command_rejects_extra_fields_and_stale_revision(tmp_path):
    labels = SessionLabels(tmp_path)
    created = labels.command({
        "action": "create_label", "expected_revision": None, "name": "PDK",
    })
    with pytest.raises(Exception, match="service message fields"):
        labels.command({
            "action": "create_label", "expected_revision": created["revision"],
            "name": "Other", "extra": True,
        })
    with pytest.raises(ValueError, match="已被修改"):
        labels.command({
            "action": "rename_label", "expected_revision": None,
            "label_id": created["labels"][0]["id"], "name": "Stale",
        })


def test_sico_tools_create_and_classify_only_the_current_session(tmp_path):
    labels = SessionLabels(tmp_path)
    loop = SimpleNamespace(
        journal=SimpleNamespace(session_id="live"),
        tools=ToolRegistry(),
        lock=threading.RLock(),
    )
    attach_label_tools(loop, labels)
    create = loop.tools.resolve("create_session_label")
    select = loop.tools.resolve("set_current_session_label")
    listing = loop.tools.resolve("list_session_labels")
    assert create and select and listing

    created = create.handler({"name": "PDK"}, None)
    label_id = created.data["labels"][0]["id"]
    assert labels.snapshot()["labels"][0]["name"] == "PDK"
    selected = select.handler({"label_id": label_id}, None)
    assert selected.data["labels"][0]["sessions"] == ["live"]
    assert listing.handler({}, None).data["labels"][0]["sessions"] == ["live"]
    removed = select.handler({"label_id": ""}, None)
    assert removed.data["labels"][0]["sessions"] == []


def test_label_mutation_publishes_without_requesting_a_catalog_rescan(tmp_path):
    labels = SessionLabels(tmp_path)
    published = []
    labels.observe(published.append)
    hub = SimpleNamespace(workspace=SimpleNamespace(labels=labels))
    result = FrontendHub.command(hub, None, "navigation_labels", {
        "action": "create_label", "expected_revision": None, "name": "PDK",
    })
    assert result["labels"][0]["name"] == "PDK"
    assert published and published[-1]["revision"] == result["revision"]


def test_catalog_publish_labels_updates_snapshot_without_scanning(tmp_path, monkeypatch):
    rows = {
        str(index): {"id": str(index), "modified": index, "name": "s" + str(index)}
        for index in range(205)
    }
    scans = []

    def scan(_index, _progress=None):
        scans.append(True)
        return rows

    monkeypatch.setattr("sico.service.catalog.CatalogIndex.scan", scan)
    service = CatalogService(tmp_path, lambda: ())
    try:
        service.request().result(5)
        count = len(scans)
        state = {
            "revision": "r",
            "labels": ({"id": "l_" + "a" * 32, "name": "Old",
                        "sessions": ("5",)},),
            "loose_order": (),
            "error": "",
        }
        service.publish_labels(state)
        snapshot = service.snapshot()
        assert snapshot["labels"]["revision"] == "r"
        assert any(row["id"] == "5" for row in snapshot["rows"])
        assert len(scans) == count
    finally:
        service.close()
        assert service.wait(5)
