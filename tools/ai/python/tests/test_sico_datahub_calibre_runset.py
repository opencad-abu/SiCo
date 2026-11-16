"""Runset identity and non-authoritative DRC catalog regressions."""

from pathlib import Path

import pytest
from sico_datahub.calibre.runset import catalog_drc_runset, runset_identity
from sico_datahub.errors import HubError


def _runset(tmp_path: Path):
    included = tmp_path / "rules.inc"
    included.write_text(
        "LAYER M1\n"
        "RULE_A { LAYER M1 }\n"
        "RULE_B { LAYER M1 }\n",
        encoding="utf-8",
    )
    root = tmp_path / "tech.cal"
    root.write_text(
        'INCLUDE "rules.inc"\n'
        "GROUP G1 RULE_A RULE_B\n",
        encoding="utf-8",
    )
    return root, included


def test_runset_identity_tracks_include_closure(tmp_path):
    root, included = _runset(tmp_path)
    first = runset_identity(root, tool_version="Calibre fixture")
    second = runset_identity(root, tool_version="Calibre fixture")
    assert first == second
    assert first.dependency_count == 2
    assert first.tool_version == "Calibre fixture"
    included.write_text(
        included.read_text(encoding="utf-8") + "LAYER M2\n",
        encoding="utf-8",
    )
    changed = runset_identity(root)
    assert changed.dependencies_sha256 != first.dependencies_sha256
    assert changed.runset.sha256 == first.runset.sha256


def test_static_catalog_indexes_groups_members_and_layers(tmp_path):
    root, _included = _runset(tmp_path)
    catalog = catalog_drc_runset(root, expand_tvf=False, tool_version="fixture")
    assert catalog.source == "static"
    assert catalog.groups == ("G1",)
    assert catalog.counts == {"G1": 2}
    assert catalog.members == {"G1": ("RULE_A", "RULE_B")}
    assert catalog.layers == ("M1",)
    assert catalog.error == ""
    assert catalog.tool_version == "fixture"
    assert len(catalog.dependencies_sha256) == 64


def test_catalog_layer_index_is_bounded(tmp_path):
    runset = tmp_path / "layers.cal"
    runset.write_text(
        "".join(f"LAYER L{index}\n" for index in range(250)),
        encoding="utf-8",
    )
    catalog = catalog_drc_runset(runset, expand_tvf=False)
    assert len(catalog.layers) == 200
    assert catalog.layers[0] == "L0"
    assert catalog.layers[-1] == "L199"


def test_catalog_rejects_missing_runset(tmp_path):
    with pytest.raises(HubError) as failure:
        catalog_drc_runset(tmp_path / "missing.cal")
    assert failure.value.code == "invalid_arguments"
