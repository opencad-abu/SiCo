"""Install-directory (file layer) index tests on a synthetic tree; no Virtuoso session."""

from pathlib import Path

import pytest

from cadai import pdk_files


def deck_text():
    return "\n".join([
        "// synthetic model deck",
        "inline subckt n09_ckt (d g s b)",
        "parameters lmin=0.03u lmax=1.8u wmin=90n wmax=2.7u jmax=2",
        "n09_ckt w=w l=l sa=sa sb=sb",
        "ends n09_ckt",
        "inline subckt n09_ckt (d g s b)",
        "ends n09_ckt",
        "model ndio09 dio",
        "ends ndio09",
        "",
    ])


def readme_text():
    return "\n".join([
        "readme",
        "| MOSFET type | name | Lmin | Wmin |",
        "|---|---|---|---|",
        "| 0.9V svt NMO | n09_ckt | 0.03um | 0.1um |",
        "| 2.5V PMOS | p25_ckt | 0.27um | 0.27um |",
        "",
    ])


def build(root: Path) -> Path:
    lib = root / "libx"
    (lib / "cell_a" / "symbol").mkdir(parents=True)
    (lib / "cell_b" / "layout").mkdir(parents=True)
    (lib / "mos.Cat").write_text('libx/cell_a type="cell"\nlibx/mos_child type="category"\n')
    models = root / "models" / "spectre"
    models.mkdir(parents=True)
    (models / "x_spe.mdl").write_text(deck_text())
    (models / "x_readme_spe.txt").write_text(readme_text())
    (root / "docs" / "SPICE_model").mkdir(parents=True)
    (root / "docs" / "SPICE_model" / "user.pdf").write_bytes(b"%PDF-1.4 synthetic")
    (root / "Calibre").mkdir()
    return root


@pytest.mark.parametrize("text,expected", [
    ("0.03um", 3e-8), ("90n", 9e-8), ("1u", 1e-6), ("1.8u", 1.8e-6), ("2.7u", 2.7e-6),
    ("1.5k", 1500.0), ("2meg", 2e6), ("2", 2.0), ("2v", 2.0), ("", None), (None, None),
    ("abc", None),
])
def test_parse_si(text, expected):
    value = pdk_files.parse_si(text)
    assert (value is None and expected is None) or value == pytest.approx(expected)


def test_model_index_reads_decks_limits_terminals_and_readme_minima(tmp_path):
    root = build(tmp_path)
    index = pdk_files.model_index(root)
    assert index["available"] is True and index["digest"]
    entry = index["models"]["n09_ckt"]
    assert entry["kind"] == "subckt" and entry["terminals"] == ["d", "g", "s", "b"]
    # Every declaration site is kept (corner-sectioned decks declare one model repeatedly);
    # parameter/comment lines are not declaration sites.
    assert [row["kind"] for row in entry["decks"]] == ["subckt", "subckt"]
    assert [row["line"] for row in entry["decks"]] == [2, 6]
    assert entry["limits"]["lmin"]["value"] == pytest.approx(3e-8)
    assert entry["limits"]["wmin"]["value"] == pytest.approx(9e-8)
    assert entry["limits"]["wmax"]["value"] == pytest.approx(2.7e-6)
    assert entry["limits"]["lmin"]["line"] == 3 and entry["limits"]["lmin"]["as_entered"] == "0.03u"
    assert set(entry["instance_args"]) == {"w", "l", "sa", "sb"}
    assert "ndio09" in index["models"] and index["models"]["ndio09"]["kind"] == "model"
    documented = index["documented"]
    assert documented["n09_ckt"]["limits"]["l"]["min"] == pytest.approx(3e-8)
    assert documented["n09_ckt"]["limits"]["l"]["as_entered"] == "0.03um"
    assert documented["n09_ckt"]["limits"]["w"]["min"] == pytest.approx(1e-7)
    assert documented["p25_ckt"]["limits"]["l"]["min"] == pytest.approx(2.7e-7)
    # Human description cells and bare numbers never become documented cells.
    assert "0.9V svt NMO" not in documented and "0.03um" not in documented
    assert index["readme_rows"]["n09_ckt"][0]["line"] == 4


def test_resolve_model_prefers_the_model_name_and_falls_back_to_the_cell(tmp_path):
    index = pdk_files.model_index(build(tmp_path))
    found = pdk_files.resolve_model(index, "n09_ckt", "n09_ckt_3t")
    assert found["status"] == "found" and found["basis"] == "model_name"
    assert found["decks"][0]["file"].endswith("x_spe.mdl") and found["terminals"]
    fallback = pdk_files.resolve_model(index, "not_a_declared_model", "n09_ckt")
    assert fallback["status"] == "found" and fallback["basis"] == "cell_name"
    missing = pdk_files.resolve_model(index, "t25ll_ckt", "nt25_ckt_3t")
    assert missing["status"] == "not_found_in_shipped_decks" and missing["decks"] == []
    assert pdk_files.resolve_model({"available": False, "models": {}}, "x", "y")["status"] == "decks_unavailable"


def test_categories_from_files_and_cell_listing(tmp_path):
    root = build(tmp_path)
    categories = pdk_files.categories_from_files(root, "libx")
    assert categories["cells"] == {"cell_a": ["mos"]}
    assert categories["categories"]["mos"]["cells"]["cell_a"]["line"] == 1
    assert categories["categories"]["mos"]["children"] == [
        {"name": "mos_child", "type": "category", "line": 2}]
    assert categories["files"] == [{"file": str(root / "libx" / "mos.Cat"), "category": "mos",
                                    "entries": 1}]
    assert pdk_files.library_cells(root, "libx") == {"cell_a": ["symbol"], "cell_b": ["layout"]}


def test_file_index_merges_models_documents_rule_paths_and_guards_the_root(tmp_path):
    root = build(tmp_path)
    index = pdk_files.file_index(root, "libx")
    assert index["available"] is True and index["library"] == "libx"
    assert index["document_entries"] == 1
    assert index["documents"][0]["relative"] == "docs/SPICE_model/user.pdf"
    assert index["documents"][0]["format"] == "pdf"
    assert [row["kind"] for row in index["rule_decks"]] == ["Calibre"]
    assert index["rule_decks"][0]["path"] == str(root / "Calibre")
    assert index["library_cells"] == {"cell_a": ["symbol"], "cell_b": ["layout"]}
    # Guards: no root, the filesystem root and a root without the library never scan anything.
    unavailable = pdk_files.file_index(None, "libx")
    assert unavailable["available"] is False and unavailable["models"] == {}
    assert pdk_files.file_index(Path("/"), "libx")["available"] is False
    absent = pdk_files.file_index(tmp_path, "absent")
    assert absent["available"] is False and absent["library_cells"] == {}
    assert pdk_files.categories_from_files(None, "libx")["cells"] == {}
    assert pdk_files.library_cells(None, "libx") == {}


def test_install_root_and_candidate_terminal_map(tmp_path):
    assert pdk_files.install_root(str(build(tmp_path) / "libx")) == tmp_path
    assert pdk_files.install_root(None) is None and pdk_files.install_root("") is None
    assert pdk_files.candidate_terminal_map(["d", "g", "s", "b"], ["d", "g", "s", "b"]) == []
    assert pdk_files.candidate_terminal_map(["D", "G", "S", "B"], ["d", "g", "s", "b"]) == []
    assert pdk_files.candidate_terminal_map(["PLUS", "MINUS", "B"], ["in", "out", "sx"]) == [
        {"cdf": "PLUS", "deck": "in", "basis": "position", "confidence": "low"},
        {"cdf": "MINUS", "deck": "out", "basis": "position", "confidence": "low"},
        {"cdf": "B", "deck": "sx", "basis": "position", "confidence": "low"},
    ]
    # Name matches stay high confidence; only the remaining terminal is positional.
    assert pdk_files.candidate_terminal_map(["PLUS", "MINUS", "B"], ["PLUS", "MINUS", "sx"]) == [
        {"cdf": "PLUS", "deck": "PLUS", "basis": "name", "confidence": "high"},
        {"cdf": "MINUS", "deck": "MINUS", "basis": "name", "confidence": "high"},
        {"cdf": "B", "deck": "sx", "basis": "position", "confidence": "low"},
    ]
    assert pdk_files.candidate_terminal_map(["a", "b"], ["a"]) == []


def test_truncated_deck_index_never_claims_a_model_is_absent(tmp_path, monkeypatch):
    root = build(tmp_path)
    monkeypatch.setattr(pdk_files, "MAX_DECK_BYTES", 8)  # below any real deck size
    index = pdk_files.model_index(root)
    assert index["available"] is True and index["truncated"] is True
    assert index["models"] == {}
    resolution = pdk_files.resolve_model(index, "n09_ckt", "cell_a")
    assert resolution["status"] == "decks_truncated" and resolution["decks"] == []
