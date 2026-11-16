"""Tier aliases and filtered searches must preserve immutable reference identity."""

import copy
import sqlite3

import pytest
from sicostate import project_directory
from test_template_library_tiers import install, query, record
from test_template_library_tiers import roots as reader_roots

from cadai.template_catalog import TemplateCatalog, call_template
from cadai.template_schema import RULE_VERSION, TemplateUnavailable, canonical

roots = reader_roots


@pytest.mark.parametrize("alias", ["root", "catalogs", "file"])
def test_physical_alias_keeps_tiers_without_repeated_scan(roots, monkeypatch, alias):
    workspace, private, project, _ = roots
    item = record("alias")
    install(project, item)
    project_directory(workspace, "ai", create=True)
    if alias == "root":
        private.symlink_to(project, target_is_directory=True)
    else:
        private.mkdir()
        if alias == "catalogs":
            (private / "catalogs").symlink_to(project / "catalogs", target_is_directory=True)
        else:
            (private / "catalog.sqlite3").symlink_to(project / "catalogs/data.sqlite3")
    catalog = TemplateCatalog(workspace=workspace)
    reads = []
    original = catalog._read

    def read(path, reference, **kwargs):
        reads.append(path)
        return original(path, reference, **kwargs)

    monkeypatch.setattr(catalog, "_read", read)
    for tier in (None, "private", "project"):
        result = query(catalog, **({"tier": tier} if tier else {}))
        assert result["total"] == 1
        row = result["templates"][0]
        assert row["catalog_origin"]["tier"] == (tier or "private")
        assert [o["tier"] for o in row["available_from"]] == ["private", "project"]
        assert all(
            s["catalog_count"] == 1 and s["status"] == "ready"
            for s in result["catalog_sources"]
            if s["tier"] in {"private", "project"}
        )
    assert not reads  # One physical shard needs no cross-file content comparison.
    assert catalog.locate(item["template_ref"])[0] == item
    assert len(reads) == len(catalog._paths()) == 1


@pytest.mark.parametrize(
    "filters",
    [{"tier": "private"}, {"tier": "project"}, {"cell": "INV"}, {"rule_version": RULE_VERSION}],
)
def test_matching_reference_conflict_cannot_hide_behind_filters(roots, filters):
    workspace, private, project, _ = roots
    item = record("filtered-conflict")
    install(private, item)
    changed = copy.deepcopy(item)
    changed["source"]["cell"] = changed["summary"]["cell"] = "OTHER"
    changed["rule_version"] = changed["summary"]["rule_version"] = "other-rule"
    install(project, changed)
    with pytest.raises(TemplateUnavailable, match="reference conflict"):
        query(TemplateCatalog(workspace=workspace), **filters)


def test_tier_selects_primary_origin_but_reports_all_copies(roots):
    workspace, private, project, builtin = roots
    item = record("copied")
    for root in (private, project, builtin):
        install(root, item)
    output = call_template("query_circuit_templates", {"tier": "project"}, workspace=workspace)
    row = output["templates"][0]
    assert row["catalog_origin"]["tier"] == "project"
    assert [o["tier"] for o in row["available_from"]] == ["private", "project", "builtin"]


def test_missing_asset_still_reports_storage_origins(roots):
    workspace, _, project, _ = roots
    item = record("no-layout")
    install(project, item)
    output = call_template(
        "get_circuit_template",
        {"template_ref": item["template_ref"], "section": "layout"},
        workspace=workspace,
    )
    assert output["code"] == "asset_unavailable"
    assert output["catalog_origin"]["tier"] == "project"


@pytest.mark.parametrize("fault", ["record_array", "summary_array", "summary_ref"])
def test_bad_catalog_record_and_summary_are_explicit_errors(roots, fault):
    workspace, _, project, _ = roots
    item = record("bad-index")
    install(project, item)
    with sqlite3.connect(str(project / "catalogs/data.sqlite3")) as db:
        if fault == "record_array":
            db.execute("UPDATE templates SET data_json='[]'")
        elif fault == "summary_array":
            db.execute("UPDATE templates SET summary_json='[]'")
        else:
            summary = {**item["summary"], "template_ref": "tpl_" + "f" * 64}
            db.execute("UPDATE templates SET summary_json=?", (canonical(summary),))
    with pytest.raises(TemplateUnavailable, match="catalog"):
        call_template(
            "get_circuit_template" if fault == "record_array" else "query_circuit_templates",
            {"template_ref": item["template_ref"]} if fault == "record_array" else {},
            workspace=workspace,
        )


@pytest.mark.parametrize("section", ["preview", "layout"])
def test_every_detail_response_obeys_byte_budget(roots, monkeypatch, section):
    from cadai import template_storage

    workspace, _, project, _ = roots
    item = record("bounded-response")
    install(project, item)
    monkeypatch.setattr(template_storage, "MAX_RESPONSE_BYTES", 100)
    with pytest.raises(TemplateUnavailable, match="response exceeds budget"):
        call_template(
            "get_circuit_template",
            {"template_ref": item["template_ref"], "section": section},
            workspace=workspace,
        )


@pytest.mark.parametrize("field", ["summary_json", "data_json"])
def test_utf8_bytes_rejected_before_json_decode(roots, monkeypatch, field):
    from cadai import template_query, template_storage

    workspace, _, project, _ = roots
    item = record("multibyte")
    item["provenance"]["note"] = item["summary"]["note"] = "模板" * 3000
    install(project, item)
    raw = canonical(item["summary"] if field == "summary_json" else item)
    budget = (len(raw) + len(raw.encode())) // 2
    assert len(raw) < budget < len(raw.encode())
    monkeypatch.setattr(template_query, "MAX_SUMMARY_BYTES", budget)
    monkeypatch.setattr(template_storage, "MAX_RECORD_BYTES", budget)

    def no_decode(raw):
        raise AssertionError("oversized catalog JSON must not reach the decoder")

    monkeypatch.setattr(template_storage, "catalog_json", no_decode)
    catalog = TemplateCatalog(workspace=workspace)
    with pytest.raises(TemplateUnavailable, match="budget exceeded|record exceeds"):
        if field == "summary_json":
            query(catalog)
        else:
            catalog.get(item["template_ref"])


def test_directory_traversal_is_bounded_before_full_enumeration(roots, monkeypatch):
    from cadai import template_storage

    workspace, _, project, _ = roots
    project.mkdir()
    for index in range(6):
        (project / (str(index) + ".txt")).touch()
    monkeypatch.setattr(template_storage, "MAX_DIRECTORY_ENTRIES", 5)
    with pytest.raises(TemplateUnavailable, match="directory scan budget"):
        query(TemplateCatalog(workspace=workspace))


def test_reference_copy_lookup_has_global_bound(roots, monkeypatch):
    from cadai import template_query

    workspace, private, project, builtin = roots
    item = record("copies")
    for root in (private, project, builtin):
        install(root, item)
    monkeypatch.setattr(template_query, "MAX_REFERENCE_COPIES", 2)
    with pytest.raises(TemplateUnavailable, match="copy lookup budget"):
        query(TemplateCatalog(workspace=workspace), tier="private")


def test_corrupt_catalog_is_reported_even_if_tier_filter_has_no_candidates(roots):
    workspace, _, project, _ = roots
    project.mkdir()
    (project / "catalog.sqlite3").write_text("bad catalog")
    with pytest.raises(TemplateUnavailable):
        call_template("query_circuit_templates", {"tier": "builtin"}, workspace=workspace)
