"""Publication tiers revalidate facts and sanitize the full source closure."""

import copy
import json
from pathlib import Path

import pytest
from template_extract_fixture import request
from template_native_content_fixture import guard
from template_qualification_fixture import collect_rows, routed_rows, symbol_rows

from cadai.template_catalog import TemplateCatalog, main
from cadai.template_schema import TemplateError, TemplateUnavailable, arguments
from cadai.template_standard.codec import encode
from cadai.template_standard.identity import reference
from cadai.template_standard.package import load, publish
from cadai.template_standard.projection import compile_projection, project
from cadai.template_standard.reader import locate, query
from cadai.template_standard.replay import engine_record
from cadai.template_standard.validate import validate


def source(tmp_path, rows=None):
    result, _ = collect_rows(tmp_path, rows or guard(routed_rows()))
    catalog = TemplateCatalog(workspace=tmp_path)
    item, _ = locate(catalog, result["template_ref"])
    policy = dict(
        masters=[
            {k: d[k] for k in ("target", "fingerprint")}
            for d in item["package"]["dependencies"].values()
            if d["kind"] == "master"
        ]
    )
    return result, catalog, item, policy


@pytest.mark.parametrize("level", ["L1", "L2", "L3"])
def test_publication_levels_private_evidence_and_deterministic_retry(tmp_path, level):
    result, catalog, item, policy = source(tmp_path)
    before = {p: (item["path"] / p).read_bytes() for p in item["observed"]}
    out = tmp_path / "public"
    projected = project(catalog, result["template_ref"], level, output=out, master_policy=policy)
    assert projected == project(
        catalog, result["template_ref"], level, output=out, master_policy=policy
    )
    assert projected["template_ref"] != result["template_ref"]
    stored = load(projected["package"])
    docs = stored["templates"]["main"]
    text = (
        encode(stored["tables"]).decode()
        + encode(docs).decode()
        + encode(stored["package"]).decode()
    )
    for secret in (
        "M0",
        "M1",
        "demo",
        "INV",
        "/pdk",
        "source_name",
        "value_skill",
        "wire_reference",
        "library_path",
    ):
        assert secret not in text
    assert "origin" not in docs["collection"]
    assert "layout" not in docs
    assert projected["capabilities"]["topology-v1"] == "passed"
    assert projected["capabilities"]["whole-reuse-v1"] == "passed"
    assert stored["tables"]["template"]["items"]["main"]["use"] == item["entry"]["use"]
    assert not list(out.rglob("*.projection-input.json"))
    assert all(
        p["roots"] == {} for p in json.loads((out / "index.json").read_bytes())["packages"].values()
    )
    assert list(catalog.root.glob("evidence/*.projection-input.json"))
    if level == "L1":
        assert docs["topology"]["masters"] == {}
        assert set(stored["package"]["dependencies"]) == {"capture"}
        assert not set(docs).intersection({"placement", "routing", "parameter", "symbol", "layout"})
        assert projected["capabilities"]["placement-reference-v1"] == "not_applicable"
    else:
        assert docs["topology"]["masters"]
        assert projected["capabilities"]["routing-reference-v1"] == "passed"
        assert ("parameter" in docs) == (level == "L3")
    assert all((item["path"] / p).read_bytes() == raw for p, raw in before.items())
    public = TemplateCatalog(root=out)
    found, _ = locate(public, projected["template_ref"])
    with pytest.raises(TemplateUnavailable, match="offline reference"):
        engine_record(found)
    rows = query(public, arguments("query_circuit_templates", dict(detail_level=level)))
    assert rows["items"][0]["template_ref"] == projected["template_ref"]


def test_unknown_or_unapproved_master_cannot_publish_l2(tmp_path):
    result, catalog, _, policy = source(tmp_path)
    for bad in (
        None,
        {"masters": []},
        {"masters": [policy["masters"][0] | dict(fingerprint="sha256:" + "0" * 64)]},
    ):
        with pytest.raises(TemplateError, match="not approved"):
            project(catalog, result["template_ref"], "L2", master_policy=bad)
    other = tmp_path / "unknown"
    other.mkdir()
    result, catalog, _, policy = source(other, routed_rows())
    with pytest.raises(TemplateError, match="fixed fingerprint"):
        project(catalog, result["template_ref"], "L3", master_policy=policy)


def test_missing_private_capture_downgrades_and_keeps_graph_provenance(tmp_path):
    result, catalog, _, _ = source(tmp_path)
    Path(result["capture"]).unlink()
    out = project(catalog, result["template_ref"], "L1")
    assert out["capabilities"]["topology-v1"] == "unknown"
    assert out["capabilities"]["whole-reuse-v1"] == "unknown"
    item, _ = locate(catalog, out["template_ref"])
    assert item["package"]["dependencies"]["capture"]["fingerprint"].startswith("sha256:")
    assert item["tables"]["sources"]["items"]["capture"]["scope_digest"].startswith("sha256:")
    assert (
        query(catalog, arguments("query_circuit_templates", dict(detail_level="L1")))["items"] == []
    )


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "resource",
        "dependency",
        "category",
        "extra_source",
        "label",
        "master",
        "digest",
        "qualification",
        "extension",
        "gap",
    ],
)
def test_reader_rejects_transitive_leaks_even_with_recomputed_identity(tmp_path, fault):
    _, _, item, policy = source(tmp_path)
    package, tables, docs, _, _ = compile_projection(item, "L1", policy, None)
    if fault == "source":
        tables["sources"]["items"]["evidence"]["locator"] = "/private/source"
    elif fault == "resource":
        tables["file"]["items"]["capture"]["path"] = "private_cell/secret.json"
    elif fault == "dependency":
        package["dependencies"]["secret"] = dict(
            kind="master",
            target=dict(library="secret", cell="x", view="symbol"),
            fingerprint="sha256:" + "0" * 64,
            method="secret",
        )
    elif fault == "category":
        tables["category"]["items"]["reference"]["name"] = "secret"
    elif fault == "extra_source":
        tables["sources"]["items"]["unused"] = tables["sources"]["items"]["evidence"] | dict(
            summary="secret"
        )
    elif fault == "label":
        docs["topology"]["devices"]["d0"]["source_name"] = "secret"
    elif fault == "master":
        docs["topology"]["devices"]["d0"]["master"]["reason"] = "secret"
    elif fault == "digest":
        tables["sources"]["items"]["capture"]["scope_digest"] = "sha256:" + "0" * 64
    elif fault == "qualification":
        docs["qualification"]["checks"]["topology-v1"]["reason"] = "secret"
    elif fault == "extension":
        package["x_secret"] = "/private/path"
    else:
        docs["collection"]["gaps"]["g0"] = dict(
            part="topology.json",
            object="secret",
            pointer="",
            code="missing_required",
            operations=["topology-v1"],
            reason="secret",
            action="complete_evidence",
        )
    ref = reference(package, tables, docs, "main")
    tables["template"]["items"]["main"]["template_ref"] = ref
    docs["qualification"]["template_ref"] = ref
    with pytest.raises(TemplateError, match="projection"):
        publish(tmp_path / "public", package, tables, {"main": docs}, roots={})
    assert not (tmp_path / "public/index.json").exists()


def test_raw_secrets_are_rebuilt_not_recursively_copied(tmp_path):
    _, _, item, policy = source(tmp_path)
    item = copy.deepcopy(item)
    item["entry"]["name"] = "secret"
    item["tables"]["sources"]["items"]["capture"]["summary"] = "/private/secret"
    item["documents"]["topology"]["devices"]["d0"]["source_name"] = "secret"
    item["documents"]["placement"]["annotations"]["private"] = dict(text="evil()")
    item["documents"]["parameter"]["items"] = {
        "private": dict(
            device="d0",
            name="w",
            type="string",
            value="private_secret",
            unit="secret",
            meaning="secret",
            role="input",
        ),
        "sizing": dict(
            device="d0",
            name="l",
            type="string",
            value="1u",
            unit="secret",
            meaning="secret",
            role="input",
        ),
        "comment": dict(
            device="d0",
            name="comment",
            type="string",
            value="secret",
            unit="1",
            meaning="secret",
            role="unknown",
        ),
    }
    package, tables, docs, proof, _ = compile_projection(item, "L3", policy, None)
    assert b"secret" in proof
    assert b"secret" not in encode(dict(package=package, tables=tables, docs=docs))
    assert len(docs["parameter"]["items"]) == 2
    assert any(p["value"] == "1u" for p in docs["parameter"]["items"].values())
    assert any(isinstance(p["value"], dict) for p in docs["parameter"]["items"].values())
    validate(package, tables, {"main": docs})


def test_cli_standard_default_and_symbol_text_removed(tmp_path, capsys):
    result, catalog, _, _ = source(tmp_path)
    main(
        [
            "project",
            "--workspace",
            str(tmp_path),
            "--template-ref",
            result["template_ref"],
            "--level",
            "L1",
            "--output",
            str(tmp_path / "public"),
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert out["template_ref"].startswith("ctpl_")
    other = tmp_path / "symbol"
    other.mkdir()
    args = request() | dict(assets=["schematic", "symbol"])
    result, _ = collect_rows(other, guard(symbol_rows(schematic=True)), args)
    catalog = TemplateCatalog(workspace=other)
    item, _ = locate(catalog, result["template_ref"])
    policy = dict(
        masters=[
            {k: d[k] for k in ("target", "fingerprint")}
            for d in item["package"]["dependencies"].values()
            if d["kind"] == "master"
        ]
    )
    out = project(catalog, result["template_ref"], "L2", master_policy=policy)
    projected, _ = locate(catalog, out["template_ref"])
    assert projected["documents"]["symbol"]["labels"] == {}
    assert out["capabilities"]["symbol-reference-v1"] == "passed"
