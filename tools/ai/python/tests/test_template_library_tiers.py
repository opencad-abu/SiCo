"""Federated references keep exact identity while all normal writes remain private."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sicostate import project_directory
from template_circuit_fixture import persisted_case
from test_circuit_templates import legacy_rows, write_capture

from cadai import template_locations
from cadai.template_build import make_template, publish
from cadai.template_capture import parse_capture
from cadai.template_catalog import TemplateCatalog, call_template
from cadai.template_circuit import prepare_template
from cadai.template_locations import template_root
from cadai.template_schema import TemplateUnavailable, arguments


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.delenv("SICO_CIRCUIT_TEMPLATES_DIR", raising=False)
    monkeypatch.delenv("CAD_CIRCUIT_TEMPLATES_DIR", raising=False)
    monkeypatch.delenv("CAD_AI_TEMPLATE_ROOT", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    builtin, project = tmp_path / "builtin", tmp_path / "shared"
    monkeypatch.setattr(template_locations, "builtin_root", lambda: builtin)
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(project))
    return workspace, template_root(workspace), project, builtin


def record(variant):
    rows = legacy_rows()
    raw = ("\n".join(json.dumps(r) for r in rows) + "\n").encode()
    return make_template(parse_capture(raw), provenance={"variant": variant})


def install(root, item, shard="data"):
    if root.name == "circuit_templates":
        template_root(root.parents[2], create=True)
    return publish([item], root / "catalogs" / (shard + ".sqlite3"))


def query(catalog, **kwargs):
    return catalog.query(arguments("query_circuit_templates", kwargs))


def fingerprint(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_union_precedence_tier_filters_and_pagination(roots):
    workspace, private, project, builtin = roots
    items = [record(v) for v in ("local", "shared", "shipped")]
    for root, item in zip([private, project, builtin], items):
        install(root, item)
    catalog = TemplateCatalog(workspace=workspace)
    output = query(catalog)
    assert output["total"] == 3  # Same cell name does not hide different versions.
    assert [r["catalog_origin"]["tier"] for r in output["templates"]] == [
        "private",
        "project",
        "builtin",
    ]
    assert all(r["status"] == "ready" for r in output["catalog_sources"])
    for index, tier in enumerate(["private", "project", "builtin"]):
        assert (
            query(catalog, tier=tier)["templates"][0]["template_ref"]
            == items[index]["template_ref"]
        )
    assert [query(catalog, offset=i, limit=1)["templates"][0] for i in range(3)] == output[
        "templates"
    ]
    assert query(catalog, offset=2, limit=1)["next_offset"] is None


def test_workspace_alias_uses_canonical_template_roots(roots):
    workspace, private, project, builtin = roots
    alias = workspace.parent / "workspace-alias"
    alias.symlink_to(workspace, target_is_directory=True)
    catalog = TemplateCatalog(workspace=alias)
    assert catalog.locations[0].path == private.resolve()
    assert all(location.path.is_absolute() and location.path == location.path.resolve()
               for location in catalog.locations)


def test_empty_template_query_is_terminal_for_direct_creation(roots):
    workspace, _, _, _ = roots
    result = query(TemplateCatalog(workspace=workspace), query="cell-that-does-not-exist")
    assert result["total"] == 0
    assert result["template_query_required"] is False
    assert result["next_action"] == "preview_circuit_spec"


def test_newer_rule_version_supersedes_older_same_topology(roots):
    workspace, private, _, _ = roots
    old = record("old-rule")
    new = record("new-rule")
    old["rule_version"] = "20260909.1"
    old["summary"]["rule_version"] = "20260909.1"
    new["rule_version"] = "20260910.4"
    new["summary"]["rule_version"] = "20260910.4"
    install(private, old, "old")
    install(private, new, "new")

    catalog = TemplateCatalog(workspace=workspace)
    result = query(catalog)
    assert result["total"] == 1
    primary = result["templates"][0]
    assert primary["template_ref"] == new["template_ref"]
    assert primary["rule_version"] == "20260910.4"
    assert primary["available_versions"] == ["20260910.4", "20260909.1"]
    assert primary["superseded_refs"] == [old["template_ref"]]
    # Exact references remain immutable and addressable after query precedence.
    assert catalog.get(old["template_ref"]) == old


def test_same_reference_deduplicates_with_all_origins(roots):
    workspace, private, project, builtin = roots
    item = record("identical")
    for root in (private, project, builtin):
        install(root, item)
    catalog = TemplateCatalog(workspace=workspace)
    result = query(catalog)
    assert result["total"] == 1
    assert [o["tier"] for o in result["templates"][0]["available_from"]] == [
        "private",
        "project",
        "builtin",
    ]
    assert catalog.get(item["template_ref"]) == item  # Location never changes immutable content.
    detail = call_template(
        "get_circuit_template", {"template_ref": item["template_ref"]}, workspace=workspace
    )
    assert detail["catalog_origin"]["tier"] == "private" and len(detail["available_from"]) == 3


def test_conflicting_fixed_reference_never_silently_overridden(roots):
    workspace, private, project, _ = roots
    item = record("conflict")
    install(private, item)
    changed = copy.deepcopy(item)
    changed["topology"]["devices"][0]["pins"][0]["net"] = None
    install(project, changed)
    catalog = TemplateCatalog(workspace=workspace)
    with pytest.raises(TemplateUnavailable, match="reference conflict"):
        catalog.get(item["template_ref"])
    with pytest.raises(TemplateUnavailable, match="reference conflict"):
        query(catalog)


def test_shared_read_and_preview_do_not_modify_shared_library(roots):
    workspace, private, project, builtin = roots
    item = record("shared")
    install(project, item)
    install(builtin, record("builtin"))
    before = [fingerprint(p) for p in (project, builtin)]
    out = call_template(
        "get_circuit_template",
        {"template_ref": item["template_ref"], "section": "preview"},
        workspace=workspace,
    )
    assert out["ok"] and out["catalog_origin"]["tier"] == "project"
    assert all(private in Path(a["path"]).parents for a in out["artifacts"])
    assert [fingerprint(p) for p in (project, builtin)] == before


def test_extraction_goes_to_private_even_with_shared_configured(roots, tmp_path):
    workspace, private, project, builtin = roots
    install(project, record("enterprise"))
    install(builtin, record("shipped"))
    before = [fingerprint(p) for p in (project, builtin)]
    spool = tmp_path / "spool"
    spool.mkdir()
    rows = legacy_rows()
    from cadai.template_classification import SEMANTICS

    rows[0]["classification_semantics"] = SEMANTICS
    for row in rows:
        if row["kind"] == "instance":
            row["builtin_library"] = None
            row.update(
                master_oa_view_type="schematicSymbol",
                schematic_available=False,
                terminal_names=["D", "G"],
            )
    path = write_capture(spool, rows)
    raw = path.read_bytes()

    class Client:
        runtime = SimpleNamespace(spool=spool)

        def call(self, method, args):
            assert method == "capture_circuit_template"
            return True, {
                "artifact": {
                    "path": str(path),
                    "size": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            }

    out = call_template(
        "extract_circuit_templates",
        {"library": "demo", "cell": "INV", "assets": ["schematic"]},
        workspace=workspace,
        client=Client(),
    )
    assert out["catalog_origin"]["tier"] == "private"
    assert private in Path(out["package"]).parents and private in Path(out["capture"]).parents
    assert [fingerprint(p) for p in (project, builtin)] == before
    # The shared tiers here are legacy catalogs, which the default search keeps
    # (marked legacy) beside the new standard capture.
    listed = query(TemplateCatalog(workspace=workspace), purpose="investigate")
    refs = {row["template_ref"] for row in listed["items"]}
    assert out["template_ref"] in refs
    assert {row["legacy"] for row in listed["items"] if row.get("legacy")} == {True}
    assert query(TemplateCatalog(workspace=workspace), data_format="legacy")["total"] == 2


def test_missing_and_empty_roots_are_visible_without_directory_creation(roots):
    workspace, private, project, builtin = roots
    result = query(TemplateCatalog(workspace=workspace))
    assert result["total"] == 0 and result["next_offset"] is None
    assert {r["status"] for r in result["catalog_sources"]} == {"missing"}
    assert not any(p.exists() for p in (private, project, builtin))
    project.mkdir()
    sources = query(TemplateCatalog(workspace=workspace))["catalog_sources"]
    assert next(r for r in sources if r["tier"] == "project")["status"] == "empty"


def test_corrupt_existing_catalog_is_not_hidden_by_fallback(roots):
    workspace, _, project, builtin = roots
    install(builtin, record("valid"))
    project.mkdir()
    (project / "catalog.sqlite3").write_text("corrupted")
    with pytest.raises(TemplateUnavailable):
        call_template("query_circuit_templates", {}, workspace=workspace)


def test_legacy_private_is_read_only_compatibility_source(roots):
    workspace, private, _, _ = roots
    legacy = workspace / ".cad/ai/reference/circuit-templates"
    (workspace / ".cad/ai").mkdir(parents=True, mode=0o700)
    (workspace / ".cad").chmod(0o700)
    item = record("old")
    install(legacy, item)
    catalog = TemplateCatalog(workspace=workspace)
    out = query(catalog)
    assert out["templates"][0]["catalog_origin"]["legacy"]
    assert out["write_root"] == str(workspace / ".cad/ai/circuit_templates")
    assert catalog.get(item["template_ref"]) == item
    assert not (workspace / ".sico").exists()


def test_environment_precedence_empty_new_value_and_legacy_aliases(roots, monkeypatch):
    workspace, _, project, _ = roots
    monkeypatch.delenv("SICO_CIRCUIT_TEMPLATES_DIR")
    monkeypatch.setenv("CAD_CIRCUIT_TEMPLATES_DIR", str(project))
    with pytest.raises(ValueError, match="removed"):
        TemplateCatalog(workspace=workspace)
    monkeypatch.setenv("CAD_AI_TEMPLATE_ROOT", str(project))
    monkeypatch.delenv("CAD_CIRCUIT_TEMPLATES_DIR")
    with pytest.raises(ValueError, match="removed"):
        TemplateCatalog(workspace=workspace)
    oldest = workspace / "older"
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(oldest))
    monkeypatch.setenv("CAD_CIRCUIT_TEMPLATES_DIR", str(project))
    monkeypatch.setenv("CAD_AI_TEMPLATE_ROOT", str(project))
    loc = TemplateCatalog(workspace=workspace).locations
    project_location = next(location for location in loc if location.tier == "project")
    assert project_location.path == oldest.resolve() and not project_location.legacy
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", "")
    assert not any(
        location.tier == "project" for location in TemplateCatalog(workspace=workspace).locations
    )
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", "relative")
    locations = TemplateCatalog(workspace=workspace).locations
    assert next(row.path for row in locations if row.tier == "project") == workspace / "relative"


def test_relative_cwd_is_launch_workspace_not_process_directory(roots, monkeypatch, tmp_path):
    workspace, private, _, _ = roots
    monkeypatch.chdir(tmp_path)
    assert template_root(workspace) == private
    assert template_root() == tmp_path / ".sico/ai/circuit_templates"


def test_duplicate_symlink_catalog_scanned_once_and_private_alias_rejected(roots, monkeypatch):
    workspace, private, project, _ = roots
    install(project, record("alias"))
    project_directory(workspace, "ai", create=True)
    private.symlink_to(project, target_is_directory=True)
    catalog = TemplateCatalog(workspace=workspace)
    assert len(catalog._paths()) == 1 and query(catalog)["total"] == 1
    with pytest.raises(TemplateUnavailable, match="overlaps"):
        catalog.ensure_private_destination()

    class NoCall:
        def call(self, *args):
            raise AssertionError("invalid private destination must fail before capture")

    with pytest.raises(TemplateUnavailable, match="overlaps"):
        call_template(
            "extract_circuit_templates",
            {"library": "demo", "cell": "INV"},
            workspace=workspace,
            client=NoCall(),
        )


@pytest.mark.parametrize("reverse", [False, True])
def test_data_directory_symlinks_cannot_redirect_writes_to_shared(roots, reverse):
    workspace, private, project, _ = roots
    template_root(workspace, create=True)
    project.mkdir()
    real, alias = (private, project) if reverse else (project, private)
    (real / "captures").mkdir()
    (alias / "captures").symlink_to(real / "captures", target_is_directory=True)
    with pytest.raises(TemplateUnavailable, match="overlaps"):
        TemplateCatalog(workspace=workspace).ensure_private_destination()


def test_adapter_digest_survives_moving_catalog_between_tiers(tmp_path, monkeypatch):
    args, _ = persisted_case(tmp_path, monkeypatch)
    monkeypatch.delenv("SICO_CIRCUIT_TEMPLATES_DIR", raising=False)
    first = prepare_template(args, workspace=tmp_path)
    old = tmp_path / ".cad/ai/reference/circuit-templates"
    private = template_root(tmp_path)
    old.rename(private)
    assert prepare_template(args, workspace=tmp_path)["preview_digest"] == first["preview_digest"]
    shared = tmp_path / "enterprise"
    private.rename(shared)
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(shared))
    assert prepare_template(args, workspace=tmp_path)["preview_digest"] == first["preview_digest"]


def test_builtin_data_and_rebuild_are_authored_and_parameter_free(tmp_path):
    import importlib.util

    root = Path(__file__).resolve().parents[2] / "reference/circuit_templates"
    spec = importlib.util.spec_from_file_location("builtin_builder", root / "build_builtin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    authored = [
        module.authored_record("resistive_divider"),
        module.authored_record("rc_lowpass"),
    ]
    catalog = TemplateCatalog(root)
    # The builtin tier now serves the installed standard payload by default, so the
    # legacy catalog record is read explicitly and the standard tier is checked too.
    assert query(catalog, library="aiassistant_templates", data_format="legacy")["total"] == 2
    assert query(catalog, library="aiassistant_templates", purpose="investigate")["total"] == 2
    for item in authored:
        assert catalog.get(item["template_ref"]) == item
        assert item["provenance"]["origin"] == "authored"
        assert not item["assets"]["schematic"]["properties"]
        assert item["source"]["status"] == "authored_reference"
