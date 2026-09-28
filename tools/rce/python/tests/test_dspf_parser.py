from __future__ import annotations

from collections import Counter
from pathlib import Path

from rcepy.dspf.parser import iter_dspf_records


FIXTURES = Path(__file__).with_name("dspf_fixtures")


def _records(name: str):
    return list(iter_dspf_records(FIXTURES / name))


def test_qrc_records_keep_connections_layers_and_model_devices_separate() -> None:
    records = _records("qrc_1_0.dspf")
    counts = Counter(record.kind for record in records)

    assert counts["net"] == 2
    assert counts["node_p"] == 2
    assert counts["node_i"] == 1
    assert counts["node_s"] == 2
    assert counts["resistor"] == 2
    assert counts["capacitor"] == 3
    assert counts["model_device"] == 2
    rin = next(record for record in records if record.fields.get("name") == "Rin")
    assert rin.fields["value"] == 1000.0
    assert rin.fields["layer"] == "1"
    assert rin.fields["length"] == 2e-6


def test_starrc_continuation_retains_physical_line_range() -> None:
    records = _records("starrc_1_3.spf")
    resistor = next(record for record in records if record.fields.get("name") == "R_b1")

    assert resistor.line_end == resistor.line_start + 1
    assert resistor.fields["length"] == 2e-6
    assert resistor.fields["width"] == 1e-6


def test_error_fixture_recovers_after_bad_values_and_unsupported_records() -> None:
    records = _records("calibre_1_5_errors.dspf")
    codes = {
        str(record.fields["code"])
        for record in records
        if record.kind == "diagnostic"
    }

    assert "invalid_element_value" in codes
    assert "negative_resistor" in codes
    assert "unsupported_device" in codes
    assert "unknown_record" in codes
    assert any(record.kind == "net" and record.fields["name"] == "Z" for record in records)


def test_invalid_utf8_is_replaced_and_reported(tmp_path: Path) -> None:
    source = tmp_path / "invalid.dspf"
    source.write_bytes(b"*|DSPF 1.0\n.SUBCKT x A\n*|NET A 0\n* bad \xff\n.ENDS\n")

    records = list(iter_dspf_records(source))

    assert any(
        record.kind == "diagnostic"
        and record.fields["code"] == "decode_replacement"
        for record in records
    )


def test_compact_connections_bad_net_cap_and_unmarked_model_device_recover(
    tmp_path: Path,
) -> None:
    source = tmp_path / "variants.dspf"
    source.write_text(
        "*|DSPF 1.0\n.SUBCKT x A 0\n*|GROUND_NET 0\n"
        "*|NET A broken\n*|P(A I 0 0 0)\nC1 A 0 1F\n"
        "RR1 A A:1 RMOD 5\n.ENDS\n",
        encoding="utf-8",
    )

    records = list(iter_dspf_records(source))
    kinds = Counter(record.kind for record in records)
    codes = {
        record.fields["code"] for record in records if record.kind == "diagnostic"
    }

    assert kinds["net"] == 1
    assert kinds["node_p"] == 1
    assert kinds["capacitor"] == 1
    assert kinds["model_device"] == 1
    assert "invalid_net_capacitance" in codes
    assert "model_device_ignored" in codes


def test_malformed_net_header_clears_previous_net_context(tmp_path: Path) -> None:
    source = tmp_path / "malformed-net.dspf"
    source.write_text(
        ".SUBCKT x GOOD NEXT 0\n"
        "*|NET GOOD 0\n"
        "Rgood GOOD GOOD:1 1\n"
        "*|NET\n"
        "Rnot_parasitic GOOD GOOD:2 2\n"
        "*|NET NEXT 0\n"
        "Rnext NEXT NEXT:1 3\n"
        ".ENDS\n",
        encoding="utf-8",
    )

    records = list(iter_dspf_records(source))
    parasitic_names = {
        record.fields["name"] for record in records if record.kind == "resistor"
    }
    codes = {
        record.fields["code"] for record in records if record.kind == "diagnostic"
    }
    boundary_at = next(
        index for index, record in enumerate(records) if record.kind == "net_boundary"
    )
    malformed_at = next(
        index
        for index, record in enumerate(records)
        if record.kind == "diagnostic" and record.fields["code"] == "malformed_net"
    )

    assert parasitic_names == {"Rgood", "Rnext"}
    assert boundary_at < malformed_at
    assert "malformed_net" in codes
    assert "model_device_ignored" in codes
