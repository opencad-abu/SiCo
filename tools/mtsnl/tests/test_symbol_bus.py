from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mtsnetlistor import publish, publication_text, publication_preflight
from mtsnetlistor.errors import SymbolTransferError
from mtsnetlistor.symbol import _report_terminals, transfer_symbol
from test_symbol import _fake_dbaccess, _setup


@pytest.mark.parametrize(
    ("report", "expected"),
    (
        ('("A" "Y")', ("A", "Y")),
        ('("DATA<7:0>" "DATA<3>" "DATA<0:7>")', ("DATA<7:0>", "DATA<3>", "DATA<0:7>")),
        ('("ADDR[0:3]" "ADDR[2]" "DATA<7:1:2>")', ("ADDR[0:3]", "ADDR[2]", "DATA<7:1:2>")),
        ('("MATRIX<1:0><3:0>" "VDD!" "VSS!" "9pin")', ("MATRIX<1:0><3:0>", "VDD!", "VSS!", "9pin")),
        (r'("DATA\\<3\\>" "quote\"pin" "path\\pin")', (r"DATA\<3\>", 'quote"pin', r"path\pin")),
        ('( "DATA<1>"\n  "DATA<0>" )', ("DATA<1>", "DATA<0>")),
        ("nil", ()),
        ("()", ()),
    ),
)
def test_terminal_report_preserves_oa_names(report: str, expected: tuple[str, ...]) -> None:
    assert _report_terminals(report, "source terminals") == expected


@pytest.mark.parametrize(
    "report",
    (
        "", "DATA<7:0>", '("DATA<7:0>"', '(DATA<7:0>)', '("A" 123)',
        '("A" ("B"))', '("A") trailing', '("A") hiQuit()', '("A" . "B")',
        '("")', '("A\nB")', '("A\x00B")', '("A\x7fB")', r'("A\nB")',
        r'("A\qB")', '("unterminated\\")',
    ),
)
def test_terminal_report_rejects_malformed_or_non_string_data(report: str) -> None:
    with pytest.raises(SymbolTransferError, match="invalid terminal"):
        _report_terminals(report, "source terminals")


@pytest.mark.parametrize("name", ("A", "DATA<7:0>", r"DATA\\<3\\>"))
def test_terminal_report_rejects_duplicate_bus_names(name: str) -> None:
    with pytest.raises(SymbolTransferError, match="duplicate terminals"):
        _report_terminals(f'("{name}" "{name}")', "source terminals")


@pytest.mark.parametrize("dialect", ("spectre", "hspiceD"))
@pytest.mark.parametrize("views", ("symbol", "text", "both"))
@pytest.mark.usefixtures("protected_worker_context")
def test_publication_accepts_bus_terminals_and_preserves_netlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dialect: str, views: str
) -> None:
    _, _, target_lib, session, request = _setup(tmp_path)
    request = replace(
        request, dialect=dialect,
        target=replace(request.target, generate_symbol_view=views != "text", generate_netlist_view=views != "symbol"),
    ).validate()
    terminals = '("DATA<1:0>" "ADDR[0:1]" "VDD!")'
    dbaccess = _fake_dbaccess(tmp_path / "dbAccess", terminals=terminals)
    root = tmp_path / "run"
    root.mkdir()
    source = root / ("inv" + request.output_suffix)
    if dialect == "spectre":
        raw = 'simulator lang=spectre\nsubckt inv DATA\\<1\\> DATA\\<0\\> \\\n    ADDR\\[0\\] ADDR\\[1\\] VDD!\nends inv\n'
    else:
        raw = '.subckt inv DATA<1> DATA<0>\n+ ADDR[0] ADDR[1] VDD!\n.ends inv\n.END\n'
    source.write_text(raw, encoding="utf-8")
    imported = []

    def fake_import(import_request: object, **kwargs: object) -> int:
        imported.append(import_request.source.read_text(encoding="utf-8"))
        return 0

    monkeypatch.setattr(publication_text, "run_import", fake_import)
    monkeypatch.setattr(publication_preflight, "find_executable", lambda _: str(dbaccess))
    monkeypatch.setattr(publication_text, "find_cds_lib_debug", lambda *args, **kwargs: None)

    result = publish.publish_bundle(request, session, netlist=source, run_dir=root, dbaccess=str(dbaccess))

    assert result.status == "succeeded", result.message
    if views != "text":
        assert result.symbol.terminal_names == ("DATA<1:0>", "ADDR[0:1]", "VDD!")
        assert (target_lib / "inv_mts" / "symbol" / "shape.marker").is_file()
    if views != "symbol":
        assert result.text.status == "succeeded"
        assert imported == [raw.replace("subckt inv ", "subckt inv_mts ").replace("ends inv\n", "ends inv_mts\n")]
    else:
        assert imported == []
    assert source.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("stage", ("source_transfer", "target"))
@pytest.mark.parametrize("changed", ('("DATA<3:0>" "Y")', '("DATA<0:7>" "Y")', '("OTHER<7:0>" "Y")'))
@pytest.mark.usefixtures("protected_worker_context")
def test_bus_width_direction_and_name_mismatch_still_fail(
    tmp_path: Path, stage: str, changed: str
) -> None:
    _, _, target_lib, session, request = _setup(tmp_path)
    dbaccess = _fake_dbaccess(
        tmp_path / "dbAccess", terminals='("DATA<7:0>" "Y")',
        **{stage + "_terminals": changed},
    )

    with pytest.raises(SymbolTransferError, match="terminals differ"):
        transfer_symbol(request, session, run_dir=tmp_path / "run", dbaccess=str(dbaccess))

    if stage == "source_transfer":
        assert not (target_lib / "inv_mts").exists()
        assert not (tmp_path / "run" / "transfer" / "oa" / "target-env.json").exists()
