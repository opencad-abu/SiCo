"""Pinned v1 outputs survive collector upgrades; continuation creates a new v2 identity."""

import json

import pytest
from template_extract_fixture import request
from template_qualification_fixture import routed_rows, symbol_rows

from cadai.template_capture import parse_capture
from cadai.template_catalog import TemplateCatalog, call_template
from cadai.template_schema import arguments, canonical
from cadai.template_standard.codec import encode, sha
from cadai.template_standard.collect import compile_package, private_bytes
from cadai.template_standard.package import publish
from cadai.template_standard.reader import locate
from cadai.template_standard.replay import replay

# Full compilation hashes measured with unmodified d6d83b1014672a70252104a3ac755a245bfa457a.
GOLDEN = {
    "routing": "62af6a029b688403fb7e5b7494bcf0ef0ca2e95ec56b39243acaedae5b3d3005",
    "symbol": "d49125e72ba933cd46d2aa8a931c07ba29ffa52208c23f195192860e23c6096f",
    "unsupported_symbol": "1e34c515a9cf83494ee3bf988e6e5638cd2b02af4b53cf9e04ee7931ef85d463",
}


@pytest.mark.parametrize("name", GOLDEN)
def test_v1_exact_outputs_replay_and_versioned_continuation(tmp_path, name):
    rows = routed_rows() if name == "routing" else symbol_rows(schematic=True)
    if name == "unsupported_symbol":
        start = next(i for i, r in enumerate(rows) if r.get("asset") == "symbol")
        next(r for r in rows[start:] if r["kind"] == "terminal")["direction"] = "switch"
    raw = ("\n".join(canonical(r) for r in rows) + "\n").encode()
    capture = parse_capture(raw)
    options = arguments("extract_circuit_templates", request() | dict(assets=rows[0]["assets"]))
    scope = dict(
        capture_sha256=capture["sha256"], options=options, category="reference", provenance={}
    )
    req = dict(scope=scope, at="2026-10-08T00:00:00+00:00")
    key = sha(canonical(scope).encode())[7:]
    package, tables, docs, _, _, report = compile_package(capture, options, req, key)
    assert (
        sha(encode(dict(package=package, tables=tables, docs=docs, report=json.loads(report))))
        == "sha256:" + GOLDEN[name]
    )
    catalog = TemplateCatalog(workspace=tmp_path)
    root = catalog.ensure_private_destination()
    private_bytes(root, "captures", ".jsonl", raw)
    (root / "evidence").mkdir(exist_ok=True)
    (root / "evidence" / (key + ".request.json")).write_text(canonical(req))
    private_bytes(root, "evidence", ".validation.json", report)
    publish(root, package, tables, {"main": docs}, roots=dict(private=str(root)))
    ref = tables["template"]["items"]["main"]["template_ref"]
    old, _ = locate(catalog, ref)
    assert replay(old)[3] == scope
    new = call_template(
        "extract_circuit_templates",
        dict(template_ref=ref, reuse=options["reuse"]),
        workspace=tmp_path,
    )
    assert new["completed_from"] == ref and new["template_ref"] != ref
    current, _ = locate(catalog, new["template_ref"])
    assert replay(current)[3]["collector"] == "sico-capture-flat-v3"
    assert (
        current["documents"]["qualification"]["checks"]["routing-reference-v1"]["result"]
        == "passed"
    )
    assert replay(locate(catalog, ref)[0])[3] == scope


@pytest.mark.parametrize(
    "name,expected",
    [
        ("routing", "4320fc738515067535ba9e2fd24fbcaf20147a1dea4d32e4a52de3d44559376b"),
        ("symbol", "ab60093d22fb1b135e27d3e3e9af103baf10999df6ee9326c06aca214c2b18f1"),
    ],
)
def test_v2_fixed_compilation_matches_unmodified_67179c01(name, expected):
    rows = routed_rows() if name == "routing" else symbol_rows(schematic=True)
    capture = parse_capture(("\n".join(canonical(r) for r in rows) + "\n").encode())
    options = arguments("extract_circuit_templates", request() | dict(assets=rows[0]["assets"]))
    scope = dict(
        collector="sico-capture-flat-v2",
        capture_sha256=capture["sha256"],
        options=options,
        category="reference",
        provenance={},
    )
    req = dict(scope=scope, at="2026-10-08T00:00:00+00:00")
    key = sha(canonical(scope).encode())[7:]
    package, tables, docs, _, _, report = compile_package(capture, options, req, key)
    assert (
        sha(encode(dict(package=package, tables=tables, docs=docs, report=json.loads(report))))
        == "sha256:" + expected
    )
