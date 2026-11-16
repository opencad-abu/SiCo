"""PAS device property table reads stay bounded and never guess permissions."""

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.pas_dpt import FIELD_LIMIT, read


class Bridge:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def call(self, expression):
        self.calls.append(expression)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def reply(**overrides):
    base = {
        "library": "smic28hkmg",
        "cell": "n09_ckt",
        "pdk_dat": True,
        "base_cdf": True,
        "dpt": True,
        "extension_ref": "pas:device-property-table:v1",
        "fields": [["minL", 3e-08], ["maxL", 1.8e-06], ["minFw", 1e-07],
                   ["maxFw", 3e-06], ["grid", None]],
        "keys": ["minL", "maxL", "minFw", "maxFw", "grid"],
        "dump": "(dpt minL 3e-08)",
    }
    base.update(overrides)
    return base


def test_read_projects_named_fields_and_quotes_the_target():
    bridge = Bridge(reply())
    result = read(bridge, "smic28hkmg", "n09_ckt")
    assert bridge.calls == ['aiPdkPasDpt("smic28hkmg" "n09_ckt")']
    assert result["pdk_dat"] is True and result["dpt"] is True
    assert result["fields"] == {"minL": 3e-08, "maxL": 1.8e-06, "minFw": 1e-07, "maxFw": 3e-06}
    assert result["keys"] == ["minL", "maxL", "minFw", "maxFw", "grid"]
    assert result["extension_ref"] == "pas:device-property-table:v1"


def test_non_pas_cell_reports_no_fields_without_failing():
    result = read(Bridge(reply(pdk_dat=False, dpt=False, fields=None, keys=None, dump=None)),
                  "smic28hkmg", "n09_ckt")
    assert result["pdk_dat"] is False and result["dpt"] is False
    assert result["fields"] == {} and result["keys"] == [] and result["diagnostic"] is None


def test_oversized_or_malformed_replies_fail_closed():
    with pytest.raises(PdkUnavailable, match='dump'):
        read(Bridge(reply(dump="x" * 4097)), "smic28hkmg", "n09_ckt")
    with pytest.raises(PdkUnavailable, match='field table'):
        read(Bridge(reply(fields=[["x", 1]] * (FIELD_LIMIT + 1))), "smic28hkmg", "n09_ckt")
    with pytest.raises(PdkUnavailable, match='field value'):
        read(Bridge(reply(fields=[["minL", ["nested"]]])), "smic28hkmg", "n09_ckt")
    with pytest.raises(PdkUnavailable, match='reply'):
        read(Bridge(reply(cell="other")), "smic28hkmg", "n09_ckt")
    with pytest.raises(PdkUnavailable, match='target'):
        read(Bridge(reply()), "smic28hkmg", "")


def test_tool_contract_is_registered_for_the_generation_workflow():
    from cadai.pdk_schema import PDK_NAMES

    assert "get_pas_dpt" in PDK_NAMES
