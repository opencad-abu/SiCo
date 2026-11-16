"""Directory data-package snapshots are content fingerprints, not governance state."""

import sqlite3

import pytest
from test_template_library_tiers import install, record

from cadai.template_catalog import TemplateCatalog
from cadai.template_schema import TemplateUnavailable


def test_plain_directory_package_reads_all_catalog_members(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    project = tmp_path / "project"
    workspace.mkdir()
    first, second = record("first"), record("second")
    install(project, first, shard="first")
    install(project, second, shard="second")
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(project))
    result = TemplateCatalog(workspace=workspace).query(
        {"offset": 0, "limit": 30, "tier": "project", "data_format": "legacy"}
    )
    assert result["total"] == 2
    assert {row["template_ref"] for row in result["templates"]} == {
        first["template_ref"], second["template_ref"]
    }
    assert result["snapshot_ref"].startswith("snp_")


def test_governance_files_are_ignored_as_package_metadata(tmp_path, monkeypatch):
    workspace, project = tmp_path / "workspace", tmp_path / "project"
    workspace.mkdir()
    item = record("ordinary")
    install(project, item)
    (project / "current.json").write_text("not a required file", encoding="utf-8")
    (project / "releases").mkdir()
    (project / "policies").mkdir()
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(project))
    result = TemplateCatalog(workspace=workspace).query(
        {"offset": 0, "limit": 10, "tier": "project", "data_format": "legacy"}
    )
    assert result["total"] == 1
    assert result["templates"][0]["template_ref"] == item["template_ref"]


def test_package_member_change_fails_closed(tmp_path):
    item = record("changed")
    install(tmp_path, item)
    catalog = TemplateCatalog(tmp_path)
    entries = catalog._entries()
    path = entries[0][0]
    # Keep the old request snapshot deliberately fixed; a changed SQLite
    # member cannot be mixed with the record cache or old summary.
    with sqlite3.connect(path) as db:
        db.execute("UPDATE metadata SET value='changed-writer' WHERE key='rule_version'")
    with pytest.raises(TemplateUnavailable, match="changed"):
        catalog._read(path, item["template_ref"])
        catalog._verify_snapshot()


def test_same_metadata_cannot_hide_changed_content(tmp_path, monkeypatch):
    from cadai import template_snapshot

    item = record("same-metadata")
    install(tmp_path, item)
    catalog = TemplateCatalog(tmp_path)
    path = catalog._entries()[0][0]
    signature = catalog._snapshot_members[path]["signature"]
    # Reproduce a metadata collision deterministically, without relying on
    # the clock resolution or update timing of the test machine.
    monkeypatch.setattr(template_snapshot, "_signature", lambda _: signature)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE metadata SET value='changed-writer' WHERE key='rule_version'")
    with pytest.raises(TemplateUnavailable, match="content changed"):
        catalog._verify_snapshot()


def test_package_symlinks_are_rejected(tmp_path):
    item = record("symlink")
    install(tmp_path, item)
    path = tmp_path / "catalogs" / "data.sqlite3"
    target = tmp_path / "outside.sqlite3"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(TemplateUnavailable, match="alias|member"):
        TemplateCatalog(tmp_path).query({"offset": 0, "limit": 10})


def test_same_ref_conflict_remains_an_error(tmp_path):
    first = record("same")
    second = record("different")
    second["template_ref"] = first["template_ref"]
    second["summary"] = {**second["summary"], "template_ref": first["template_ref"]}
    install(tmp_path, first, shard="first")
    install(tmp_path, second, shard="second")
    with pytest.raises(TemplateUnavailable, match="reference conflict"):
        TemplateCatalog(tmp_path).query({"offset": 0, "limit": 10})
