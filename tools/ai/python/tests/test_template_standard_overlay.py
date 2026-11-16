"""Overlay core: change validation, merge semantics and deterministic publication."""

import copy
import json
from pathlib import Path

import pytest
from template_qualification_fixture import collect_rows, symbol_rows

from cadai.template_schema import TemplateError
from cadai.template_standard import overlay
from cadai.template_standard.codec import logical, sha


def _base(tmp_path, schematic=True):
    result, item = collect_rows(tmp_path, symbol_rows(schematic=schematic))
    return result, item, Path(item["path"])


def _dependencies(item):
    return {"capture": item["package"]["dependencies"]["capture"]["fingerprint"]}


def _path(item, role):
    return item["entry"]["dir"] + "/" + role + ".json"


def _label_text_change(item, text="renamed"):
    symbol = item["documents"]["symbol"]
    label = next(iter(symbol["labels"]))
    return symbol, label, {
        "expected": logical(symbol),
        "dependencies": _dependencies(item),
        "ops": [{"op": "replace", "path": "/labels/%s/text" % label, "value": text}],
    }


def test_overlay_merge_reference_and_qualification_rules(tmp_path):
    result, item, base_root = _base(tmp_path)
    _, label, record = _label_text_change(item)
    changes = {"items": {_path(item, "symbol"): record}}
    destination = tmp_path / "ws" / "family.local"
    package = overlay.publish(destination, base_root, changes, at="2026-10-11T00:00:00+00:00")
    assert package["format"] == overlay.FORMAT
    assert overlay.publish(destination, base_root, changes) == package  # idempotent
    merged = overlay.merge(base_root, destination)
    assert merged["changed"] == ["main"]
    entry = merged["tables"]["template"]["items"]["main"]
    assert entry["template_ref"] != result["template_ref"]
    assert merged["templates"]["main"]["symbol"]["labels"][label]["text"] == "renamed"
    qualification = merged["templates"]["main"]["qualification"]
    assert qualification["template_ref"] == entry["template_ref"]
    assert {row["result"] for row in qualification["checks"].values()} == {"unknown"}


def test_expected_and_dependency_preconditions(tmp_path):
    result, item, base_root = _base(tmp_path)
    _, label, record = _label_text_change(item)

    def build(**over):
        value = copy.deepcopy(record)
        value.update(over)
        return {"items": {_path(item, "symbol"): value}}

    cases = [
        (build(expected="sha256-jcs:" + "0" * 64), "expected digest"),
        (build(dependencies={}), "drops declared dependencies"),
        (build(dependencies={"capture": "sha256:" + "1" * 64}), "fingerprint is stale"),
    ]
    for index, (changes, message) in enumerate(cases):
        with pytest.raises(TemplateError, match=message):
            overlay.publish(tmp_path / ("bad%d" % index), base_root, changes)


def test_ops_subset_rejects_member_violations(tmp_path):
    result, item, base_root = _base(tmp_path)
    _, label, record = _label_text_change(item)

    def build(ops):
        value = copy.deepcopy(record)
        value["ops"] = ops
        return {"items": {_path(item, "symbol"): value}}

    cases = [
        ([{"op": "add", "path": "/labels/%s/text" % label, "value": "x"}], "existing member"),
        ([{"op": "replace", "path": "/labels/%s/missing" % label, "value": "x"}], "missing member"),
        ([{"op": "remove", "path": "/labels/%s/missing" % label}], "missing member"),
        ([{"op": "add", "path": "/labels/%s/bbox/0" % label, "value": 1}], "object member"),
        ([{"op": "move", "path": "/labels/%s/text" % label}], "unsupported overlay op"),
        ([{"op": "replace", "path": "labels/%s/text" % label, "value": "x"}], "object member"),
    ]
    for index, (ops, message) in enumerate(cases):
        with pytest.raises(TemplateError, match=message):
            overlay.publish(tmp_path / ("ops%d" % index), base_root, build(ops))


def test_derived_fields_and_qualification_are_not_editable(tmp_path):
    result, item, base_root = _base(tmp_path)
    template = item["tables"]["template"]
    with pytest.raises(TemplateError, match="derived"):
        overlay.publish(
            tmp_path / "p1",
            base_root,
            {
                "items": {
                    "template.json": {
                        "expected": logical(template),
                        "dependencies": _dependencies(item),
                        "ops": [
                            {
                                "op": "replace",
                                "path": "/items/main/template_ref",
                                "value": "ctpl_" + "0" * 64,
                            }
                        ],
                    }
                }
            },
        )
    symbol, label, record = _label_text_change(item)
    with pytest.raises(TemplateError, match="re-issued"):
        overlay.publish(
            tmp_path / "p2",
            base_root,
            {
                "items": {
                    _path(item, "qualification"): {
                        "expected": logical(item["documents"]["qualification"]),
                        "dependencies": {"capture": _dependencies(item)["capture"]},
                        "ops": [{"op": "replace", "path": "/checks", "value": {}}],
                    }
                }
            },
        )


def test_dependency_refresh_through_updates(tmp_path):
    result, item, base_root = _base(tmp_path)
    _, label, record = _label_text_change(item)
    refreshed = copy.deepcopy(item["package"]["dependencies"]["capture"])
    refreshed["fingerprint"] = "sha256:" + "a" * 64
    changes = {
        "dependency_updates": {"capture": refreshed},
        "items": {
            _path(item, "symbol"): dict(record, dependencies={"capture": refreshed["fingerprint"]})
        },
    }
    destination = tmp_path / "rd"
    overlay.publish(destination, base_root, changes)
    merged = overlay.merge(base_root, destination)
    assert merged["package"]["dependencies"]["capture"]["fingerprint"] == refreshed["fingerprint"]
    assert merged["changed"] == ["main"]


def test_resolve_base_and_effective_revision(tmp_path):
    result, item, base_root = _base(tmp_path)
    base = dict(
        package_id=item["package"]["package_id"],
        revision=item["package"]["revision"],
        manifest_digest=sha((base_root / "package.json").read_bytes()),
    )
    assert overlay.resolve(base, [base_root]) == base_root
    with pytest.raises(TemplateError, match="not installed"):
        overlay.resolve(dict(base, manifest_digest="sha256:" + "0" * 64), [base_root])
    revision = overlay.effective_revision("sha256:" + "1" * 64, "sha256:" + "2" * 64)
    assert revision.startswith("sha256-jcs:")
    assert len(revision) == len("sha256-jcs:") + 64


def test_overlay_manifest_detects_tampering(tmp_path):
    result, item, base_root = _base(tmp_path)
    _, label, record = _label_text_change(item)
    changes = {"items": {_path(item, "symbol"): record}}
    destination = tmp_path / "tamper"
    overlay.publish(destination, base_root, changes)
    path = destination / "changes.json"
    value = json.loads(path.read_text())
    path_key = next(iter(value["items"]))
    value["items"][path_key]["ops"][0]["value"] = "tampered"
    path.write_text(json.dumps(value))
    with pytest.raises(TemplateError, match="digest mismatch"):
        overlay.read_overlay(destination)


def _identity(item):
    origin = item["documents"]["collection"]["origin"]
    return origin["library"], origin["cell"]


def _rows(catalog, item, limit=20):
    from cadai.template_standard.reader import query

    library, cell = _identity(item)
    return query(catalog, dict(purpose="investigate", library=library, cell=cell, limit=limit))


def test_reader_serves_registered_overlay_without_redirecting_the_base(tmp_path):
    from cadai.template_catalog import TemplateCatalog
    from cadai.template_standard.reader import locate

    result, item = collect_rows(tmp_path, symbol_rows(schematic=True))
    base_root = Path(item["path"])
    symbol, label, record = _label_text_change(item)
    old_text = symbol["labels"][label]["text"]
    overlay.publish(
        base_root.parent / "family.local",
        base_root,
        {"items": {_path(item, "symbol"): record}},
        register=True,
    )
    catalog = TemplateCatalog(workspace=tmp_path)
    refs = {row["template_ref"] for row in _rows(catalog, item)["items"]}
    assert result["template_ref"] in refs
    new_ref = next(ref for ref in refs if ref != result["template_ref"])
    old, _ = locate(catalog, result["template_ref"])
    assert old["documents"]["symbol"]["labels"][label]["text"] == old_text
    new, _ = locate(catalog, new_ref)
    assert new["documents"]["symbol"]["labels"][label]["text"] == "renamed"
    checks = new["documents"]["qualification"]["checks"]
    assert {row["result"] for row in checks.values()} == {"unknown"}


def test_overlay_base_mismatch_is_rejected_at_read(tmp_path):
    from cadai.template_catalog import TemplateCatalog

    result, item = collect_rows(tmp_path, symbol_rows(schematic=True))
    base_root = Path(item["path"])
    _, _, record = _label_text_change(item)
    destination = base_root.parent / "family.local"
    overlay.publish(destination, base_root, {"items": {_path(item, "symbol"): record}}, register=True)
    catalog = TemplateCatalog(workspace=tmp_path)
    _rows(catalog, item)  # warm the derived cache
    value = json.loads((destination / "package.json").read_text())
    value["base"]["manifest_digest"] = "sha256:" + "0" * 64
    (destination / "package.json").write_text(json.dumps(value))
    with pytest.raises(TemplateError, match="base is not installed"):
        _rows(catalog, item)


def test_tampered_overlay_is_rejected_at_read(tmp_path):
    from cadai.template_catalog import TemplateCatalog

    result, item = collect_rows(tmp_path, symbol_rows(schematic=True))
    base_root = Path(item["path"])
    _, _, record = _label_text_change(item)
    destination = base_root.parent / "family.local"
    overlay.publish(destination, base_root, {"items": {_path(item, "symbol"): record}}, register=True)
    catalog = TemplateCatalog(workspace=tmp_path)
    _rows(catalog, item)  # warm the derived cache
    value = json.loads((destination / "changes.json").read_text())
    key = next(iter(value["items"]))
    value["items"][key]["ops"][0]["value"] = "tampered"
    (destination / "changes.json").write_text(json.dumps(value))
    with pytest.raises(TemplateError, match="digest mismatch"):
        _rows(catalog, item)


def test_workspace_overlay_over_project_baseline(tmp_path, monkeypatch):
    from cadai.template_catalog import TemplateCatalog
    from cadai.template_locations import PROJECT_ENV, template_root
    from cadai.template_standard.reader import locate

    shared = tmp_path / "shared"
    workspace = tmp_path / "ws"
    shared.mkdir()
    workspace.mkdir()
    result, item = collect_rows(shared, symbol_rows(schematic=True))
    base_root = Path(item["path"])
    monkeypatch.setenv(PROJECT_ENV, str(base_root.parent))
    _, label, record = _label_text_change(item)
    overlay.publish(
        template_root(workspace, create=True) / "family.local",
        base_root,
        {"items": {_path(item, "symbol"): record}},
        register=True,
    )
    catalog = TemplateCatalog(workspace=workspace)
    refs = {row["template_ref"] for row in _rows(catalog, item)["items"]}
    assert result["template_ref"] in refs
    new_ref = next(ref for ref in refs if ref != result["template_ref"])
    base_item, _ = locate(catalog, result["template_ref"])
    assert base_item["documents"]["symbol"]["labels"][label]["text"] != "renamed"
    merged, _ = locate(catalog, new_ref)
    assert merged["documents"]["symbol"]["labels"][label]["text"] == "renamed"
