"""D5 netlist terminal mapping: deck-confirmed order, never a silent port-order fallback."""

import pytest

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.pdk_binding_data import deck_order, terminal_map


def device(ports, deck=None, same_set=None):
    fold = lambda values: [value.casefold() for value in values]
    return {"ports": {"items": [{"name": name} for name in ports]},
            "port_crosscheck": {"deck": list(deck or []),
                                "same_set": fold(ports) == fold(deck or [])
                                if same_set is None else same_set}}


def test_matching_deck_names_need_no_map():
    assert terminal_map(device(["d", "g", "s", "b"], ["d", "g", "s", "b"]), {}) == ({}, "matched_by_name")


def test_missing_deck_evidence_stays_unverified():
    assert terminal_map(device(["PLUS", "MINUS"]), {}) == ({}, "unverified")


def test_ambiguous_deck_names_require_a_confirmed_map():
    with pytest.raises(CircuitSpecError, match="netlist_terminal_map"):
        terminal_map(device(["PLUS", "MINUS", "B"], ["in", "out", "sx"]), {})


def test_confirmed_map_must_permute_the_deck_terminals():
    mapping = {"PLUS": "in", "MINUS": "out", "B": "sx"}
    args = {"netlist_terminal_map": mapping}
    assert terminal_map(device(["PLUS", "MINUS", "B"], ["in", "out", "sx"]), args) == (
        mapping, "confirmed")
    # Deck evidence absent: the explicit choice is still recorded and marked confirmed.
    assert terminal_map(device(["PLUS", "MINUS", "B"]), args) == (mapping, "confirmed")


@pytest.mark.parametrize("mapping,message", [
    ({"PLUS": "in", "MINUS": "out"}, "cover exactly"),
    ({"PLUS": "in", "MINUS": "in", "B": "sx"}, "one-to-one"),
    ({"PLUS": "in", "MINUS": "out", "B": "m1"}, "permute"),
    ({"PLUS": "", "MINUS": "out", "B": "sx"}, "terminal names"),
])
def test_invalid_maps_are_rejected(mapping, message):
    with pytest.raises(CircuitSpecError, match=message):
        terminal_map(device(["PLUS", "MINUS", "B"], ["in", "out", "sx"]),
                     {"netlist_terminal_map": mapping})


def test_deck_order_follows_the_confirmed_mapping():
    terms = [{"name": "PLUS"}, {"name": "MINUS"}, {"name": "B"}]
    assert [row["name"] for row in deck_order(terms, {"PLUS": "sx", "MINUS": "in", "B": "out"},
                                              ["in", "out", "sx"])] == ["MINUS", "B", "PLUS"]
    assert deck_order(terms, {}, ["in", "out", "sx"]) == terms
    assert deck_order(terms, {}, []) == terms
