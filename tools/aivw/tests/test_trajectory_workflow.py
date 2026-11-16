from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aivworkbench.executor import ExecutorContext, ExecutorResult
from aivworkbench.executors.characterization_import import run_characterization_import
from aivworkbench.executors.trajectory_measurements import run_trajectory_measurements
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.trajectory_evidence import public_trajectories
from aivworkbench.workspace import SplitRunPaths, sha256_file


ROOT = Path(__file__).resolve().parents[3]
RUNS = {
    "master": "20260907T102305.738936Z-recipe-run-2b97ad9d011d",
    "aon": "20260907T102305.715304Z-recipe-run-f5d4dcb80a87",
}


def context(tmp_path, cell="master"):
    payload = tmp_path/"payload"
    payload.mkdir()
    run = SplitRunPaths("test", tmp_path, payload, tmp_path/"manifest.json", payload/"manifest.json")
    recipe = load_builtin_recipe("ldo."+cell+"_dynamic_model_v3")
    return ExecutorContext(recipe, run, "public_data", "evidence.import_characterization", {}, {}, {}, 10.)


@pytest.mark.parametrize("cell", ["master", "aon"])
def test_live_public_import_and_measurements_preserve_originals(tmp_path, cell):
    if not (ROOT/".aivw/runs"/RUNS[cell]/"manifest.json").is_file():
        pytest.skip("site-specific public EDA evidence unavailable")
    ctx = context(tmp_path, cell)
    ctx.gate_root.mkdir(parents=True)
    result = run_characterization_import(ctx)
    assert result.status == "PASS"
    ctx = ExecutorContext(ctx.recipe, ctx.run, "measurements", "electrical.trajectory_measurements",
                          {"any_producer_name": result}, {}, {}, 10.)
    ctx.gate_root.mkdir(parents=True)
    measured = run_trajectory_measurements(ctx)
    assert measured.status == "PASS"
    assert measured.summary["product_current_timing_status"] == "BLOCKED_CONTRACT"
    plan, evidence, waves = public_trajectories(ctx)
    assert len(waves) == (5 if cell == "master" else 7)
    assert evidence["import_provenance"]["live_source_recheck"] == "NOT_PERFORMED"
    # A valid CSV with a changed number must still fail its producer hash.
    csv = next(p for p in result.artifacts if p.name == "waveforms.csv")
    csv.write_text(csv.read_text().replace("time[s]", "time[ms]", 1))
    assert run_trajectory_measurements(ctx).status == "BLOCKED_EVIDENCE"


def test_public_import_rejects_unapproved_visibility_before_read(tmp_path):
    ctx = context(tmp_path)
    pin = tmp_path/"pin.json"
    pin.write_text(json.dumps({"schema_version": 1, "control_manifest": "/does/not/exist",
                               "sha256": "a"*64, "dataset": "hidden_holdout"}))
    recipe = SimpleNamespace(input_path=lambda name: pin)
    ctx = ExecutorContext(recipe, ctx.run, "public_data", ctx.executor_name, {}, {}, {}, 10.)
    assert run_characterization_import(ctx).status == "BLOCKED_EVIDENCE"


def test_measurement_needs_exactly_one_successful_public_producer(tmp_path):
    ctx = context(tmp_path)
    assert run_trajectory_measurements(ctx).status == "BLOCKED_EVIDENCE"


def test_recipe_generation_mode_is_explicit_and_model_plugin_is_generic():
    from aivworkbench.registry import builtin_registry
    registry = builtin_registry()
    for cell in ("master", "aon"):
        recipe = load_builtin_recipe("ldo."+cell+"_dynamic_model_v3")
        assert recipe.payload["model"]["generation"] == "registered_identification"
        assert recipe.model_class == "electrical.sampled_state"
        assert all(registry.require_executor(g["executor"]).handler for g in recipe.payload["workflow"]["gates"])
