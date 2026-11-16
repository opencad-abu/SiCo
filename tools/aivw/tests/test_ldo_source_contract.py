"""Contract evidence tests use public numeric vectors, never EDA substitutes."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from aivworkbench.cadence_statedb import engineering_number, read_statedb
from aivworkbench.ldo import canonicalize_interface, match_interface
from aivworkbench.ldo_source_contract import read_contract, verify_readback, verify_state
from aivworkbench.model import ModelGenerationRequest
from aivworkbench.plugins.linear_regulator import generate_linear_regulator_candidate
from aivworkbench.profiles import product_root
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.recipe_dag import workflow_dependency_order
from aivworkbench.spectre_data import measure_csv, stage_models, verify_model_sources, validate_psf_units
from aivworkbench.workspace import sha256_file


BASE = product_root() / "recipes/ldo"


@pytest.mark.parametrize("text,expected", [("5m", .005), ("1.001m", .001001), ("10K", 10000),
                                         ("2M", 2000000), ("2m", .002), ("-1e-12", -1e-12)])
def test_engineering_units_are_exact_and_case_sensitive(text, expected):
    assert engineering_number(text) == expected


@pytest.mark.parametrize("value", [True, 5, "5ms", "1u+2u", "NaN", "inf", "1e999", "$(cmd)"])
def test_units_reject_expressions_and_nonfinite(value):
    with pytest.raises(ValueError):
        engineering_number(value)


def test_statedb_is_not_a_result_or_acceptance_authority(tmp_path):
    path = tmp_path / "active.state"
    path.write_text('''<statedb version="5"><Test Name="source">
      <component Name="adeInfo"><field Name="designInfo" Type="list">("lib" "cell")</field></component>
      <component Name="analyses"><analyses><analysis Name="tran">
        <partition Name="fields"><field Name="enable" Type="list">(t)</field><field Name="stop" Type="string">"5m"</field></partition>
        <partition Name="options"/></analysis></analyses></component>
      <component Name="modelSetup"><field Name="modelFiles" Type="list">(("model.scs" "tt"))</field></component>
      <component Name="simulatorOptions"><partition Name="opts"><field Name="reltol" Type="string">"1e-3"</field></partition></component>
      </Test></statedb>''')
    result = read_statedb(path)
    assert result["analyses"]["tran"]["enabled"]
    assert result["simulator_options"]["reltol"] == "1e-3"
    assert result["acceptance_tolerances"] is None and result["result_authority"] is False
    path.write_text(path.read_text().replace('<statedb', '<!DOCTYPE a><statedb'))
    with pytest.raises(ValueError, match="XML"):
        read_statedb(path)


def test_formal_interface_and_fixture_cannot_match():
    source = json.loads((BASE / "source_interface_contract.json").read_text())
    fixture = json.loads((BASE / "interface_contract.json").read_text())
    for cell in ("LDO_MASTER", "LDO_AON"):
        interface = canonicalize_interface(source, cell=cell)
        assert interface.port_map["VDD"].direction == interface.port_map["VSS"].direction == "input"
        assert ("EN" in interface.port_map) == (cell == "LDO_AON")
        assert not match_interface(interface, fixture["variants"][cell]).matched
        assert list(interface.port_order) != source["variants"][cell]["spectre_term_order"]


def test_real_recipe_does_not_use_fixture_behavior():
    recipe = load_builtin_recipe("ldo.linear_regulator")
    assert recipe.revision == 3
    closure = workflow_dependency_order(recipe.payload["workflow"], ["measurement"])
    assert set(closure) == {"snapshot", "config_binding", "experiment_contract", "golden", "measurement"}
    spec = json.loads(recipe.input_path("behavior_spec").read_text())
    with pytest.raises(ValueError, match="block characterization"):
        generate_linear_regulator_candidate(ModelGenerationRequest(
            contract=json.loads(recipe.input_path("interface_contract").read_text()), spec=spec,
            contract_sha256="a" * 64, spec_sha256="b" * 64, generated_at="test"))


@pytest.mark.parametrize("mutate", [
    lambda c: c["analysis"].update(stop_s=float("nan")),
    lambda c: c["analysis"].update(stop_s=10),
    lambda c: c["model"].update(relative_path="../model.scs"),
    lambda c: c["measurements"].update(acceptance_tolerances={"vout": .01}),
    lambda c: c["measurements"]["signals"].update(VDD_INT="mV"),
    lambda c: c.update(behavioral_qualification=True),
    lambda c: c["netlisting"].update(mode="a"),
])
def test_contract_cannot_silently_expand_or_adopt_fixture_tolerances(tmp_path, mutate):
    contract = read_contract(BASE / "experiment/test_ldo_nominal.json")
    mutate(contract)
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract))
    with pytest.raises(ValueError):
        read_contract(path)


@pytest.mark.parametrize("mutate", [
    lambda c: c["measurements"].update(sample_times_s="0.001"),
    lambda c: c["stimulus"]["enable"][0]["points"].__setitem__(0, [0.1, 0]),
    lambda c: c["loads"][0].update(resistance_ohm=0),
    lambda c: c["observations"]["LDO_AON"].update(extra="unexpected"),
    lambda c: c.update(unsupported=[1]),
    lambda c: c["source"].update(state_sha256=1),
    lambda c: c["model"].update(section=1),
])
def test_contract_rejects_malformed_nested_source_fields(tmp_path, mutate):
    contract = read_contract(BASE / "experiment/test_ldo_nominal.json")
    mutate(contract)
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract))
    with pytest.raises(ValueError):
        read_contract(path)


def test_model_graph_hashes_transitive_and_self_section_includes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.scs").write_text('include "device.scs" section=tt\n')
    (source / "device.scs").write_text('include "device.scs" section = core\ninclude "core.scs" section=x\n')
    (source / "core.scs").write_text('section x\nendsection x\n')
    records = stage_models(source, "main.scs", tmp_path / "models")
    assert len(records) == 3
    verify_model_sources(records)
    (source / "core.scs").write_text('changed')
    with pytest.raises(ValueError, match="changed"):
        verify_model_sources(records)
    staged = tmp_path / "staged"
    records = stage_models(source, "main.scs", staged)
    (staged / "core.scs").write_text("tampered")
    with pytest.raises(ValueError, match="staged model"):
        verify_model_sources(records, staged)


@pytest.mark.parametrize("include", ['../escape.scs', '/absolute.scs', '$MODEL/device.scs'])
def test_model_include_path_escape_fails(tmp_path, include):
    (tmp_path / "main.scs").write_text(f'include "{include}" section=tt\n')
    with pytest.raises(ValueError):
        stage_models(tmp_path, "main.scs", tmp_path / "output")


def test_csv_interpolates_and_never_claims_behavior_pass(tmp_path):
    path = tmp_path / "wave.csv"
    path.write_text('time[s],vout[V],current[A]\n0,0,0\n0.001,2,-0.002\n0.002,0,0\n')
    result = measure_csv(path, {"vout": "V", "current": "A"}, [.0005, .0015], .002)
    assert result["samples"][0]["values"] == {"vout": 1, "current": -.001}
    assert result["correlation_status"] == "BLOCKED_CONTRACT"
    assert result["behavior_verdict"] == "NOT_ESTABLISHED"


@pytest.mark.parametrize("text", [
    'time[ms],vout[V]\n0,0\n1,2\n',
    'time[s],vout[V]\n0,0\n0.5,nan\n1,2\n',
    'time[s],vout[V]\n0,0\n0,1\n1,2\n',
    'time[s],vout[V]\n0,0\n0.5,1\n',
    'time[s],vout[V]\n0,0\n1\n',
])
def test_measurement_rejects_wrong_units_missing_or_bad_samples(tmp_path, text):
    path = tmp_path / "wave.csv"
    path.write_text(text)
    with pytest.raises(ValueError):
        measure_csv(path, {"vout": "V"}, [.5], 1)


def test_psf_unit_metadata_must_match_contract(tmp_path):
    path = tmp_path / "tran"
    path.write_text('''HEADER
"analysis type" "tran"
TYPE
"V" FLOAT DOUBLE PROP(
"units" "V"
)
SWEEP
"time" "sweep" PROP(
"units" "s"
)
TRACE
"out" "V"
VALUE
END
''')
    validate_psf_units(path, {"out": "V"})
    with pytest.raises(ValueError, match="units"):
        validate_psf_units(path, {"out": "A"})


def test_changed_ade_state_is_not_recalibrated_silently(tmp_path):
    path = tmp_path / "state.xml"
    path.write_text('changed')
    with pytest.raises(ValueError, match="recalibration"):
        verify_state(path, read_contract(BASE / "experiment/test_ldo_nominal.json"))
