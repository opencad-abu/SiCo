"""config request cases regressions."""

from __future__ import annotations
import json
from pathlib import Path
import pytest
from mtsnetlistor.config import (
    canonical_request_digest,
    canonical_request_json,
    load_request,
    request_to_dict,
    save_request,
)
from mtsnetlistor.errors import RequestValidationError
from config_fixtures import (
    _write_request,
)


def test_request_toml_is_canonical_and_does_not_contain_target_cdslib(tmp_path: Path) -> None:
    request_path, _ = _write_request(tmp_path)
    request = load_request(request_path)
    encoded = canonical_request_json(request)
    payload = json.loads(encoded)
    assert payload["source"]["cds_lib"].startswith("/")
    assert "target_cds_lib" not in encoded
    assert len(canonical_request_digest(request)) == 64
    assert payload["process_options"]["gmin"] == "1e-12"


def test_legacy_request_defaults_overwrite_controls_off_and_digest_is_stable(
    tmp_path: Path,
) -> None:
    request_path, _ = _write_request(tmp_path)
    legacy = load_request(request_path)
    assert legacy.target.overwrite_symbol_view is False
    assert legacy.target.overwrite_netlist_view is False
    legacy_digest = canonical_request_digest(legacy)
    payload = request_to_dict(legacy)
    assert "overwrite_symbol_view" not in payload["publish"]
    assert "overwrite_netlist_view" not in payload["publish"]

    request_path.write_text(
        request_path.read_text(encoding="utf-8")
        + "\n[publish]\n"
        + "generate_symbol_view = false\n"
        + "generate_netlist_view = false\n"
        + "overwrite_symbol_view = false\n"
        + "overwrite_netlist_view = false\n",
        encoding="utf-8",
    )
    explicit_false = load_request(request_path)
    assert explicit_false.target.overwrite_symbol_view is False
    assert explicit_false.target.overwrite_netlist_view is False
    assert canonical_request_digest(explicit_false) == legacy_digest


def test_unknown_keys_and_missing_environment_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request_path, _ = _write_request(tmp_path, extra="unknown = true")
    with pytest.raises(RequestValidationError, match="unknown key"):
        load_request(request_path)
    request_path, _ = _write_request(tmp_path)
    text = request_path.read_text(encoding="utf-8").replace("cds_lib = \"", "cds_lib = \"$MTS_MISSING/")
    request_path.write_text(text, encoding="utf-8")
    monkeypatch.delenv("MTS_MISSING", raising=False)
    with pytest.raises(RequestValidationError, match="undefined environment"):
        load_request(request_path)


def test_multi_cell_request_round_trips_cell_specific_settings(tmp_path: Path) -> None:
    request_path, cds = _write_request(tmp_path)
    model = cds.parent / "cell-model.lib"
    model.write_text("model", encoding="utf-8")
    request_path.write_text(
        request_path.read_text(encoding="utf-8")
        + f'''\n[[cells]]\nlibrary = "work"\ncell = "second"\nview = "schematic"\nmodels = []\n[cells.process_options]\ntemp = 85\n\n[[cells]]\nlibrary = "work"\ncell = "third"\nview = "layout"\n[[cells.models]]\nfile = "{model}"\nsection = "ss"\n''',
        encoding="utf-8",
    )
    loaded = load_request(request_path)
    assert [item.cell for item in loaded.selected_cells] == ["second", "third"]
    assert loaded.selected_cells[0].process_options.temp == 85
    assert loaded.selected_cells[1].models[0].section == "ss"
    payload = request_to_dict(loaded)
    assert [item["cell"] for item in payload["cells"]] == ["second", "third"]


def test_saved_legacy_single_cell_configuration_stays_in_legacy_form(
    tmp_path: Path,
) -> None:
    request_path, _ = _write_request(tmp_path)
    request = load_request(request_path)

    destination = save_request(request, tmp_path / "legacy-saved.toml")
    text = destination.read_text(encoding="utf-8")
    loaded = load_request(destination)

    assert "[[cells]]" not in text
    assert loaded.cell_specs == ()
    assert request_to_dict(loaded) == request_to_dict(request)
    assert canonical_request_digest(loaded) == canonical_request_digest(request)
