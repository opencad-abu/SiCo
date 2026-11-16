from __future__ import annotations

from copy import deepcopy
from pathlib import Path


from aivworkbench.executor import ExecutorContext, ExecutorResult
from aivworkbench.executors.xcelium import (
    _compare_evidence_binding,
    _validate_evidence_binding,
    _xrun_version,
    run_rnm_check,
)
from aivworkbench.l1 import build_l1_test_plan
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.workspace import (
    allocate_split_run,
    resolve_launch_paths,
    stable_digest,
)
from xcelium_l1_fixtures import _plan

import pytest


@pytest.mark.parametrize(
    "tamper",
    ("remove_case", "relax_tolerance", "change_expected_metric"),
)
def test_xcelium_rejects_generation_plan_tampering_before_subprocess(
    tmp_path: Path, tamper: str
) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    launch = tmp_path / "launch"
    payload = tmp_path / "payload"
    launch.mkdir()
    payload.mkdir()
    run = allocate_split_run(
        resolve_launch_paths(launch),
        payload,
        library="lib",
        cell="cell",
        view="rnm",
        kind="recipe",
        request_digest=stable_digest({"tamper": tamper}),
    )
    plan = deepcopy(build_l1_test_plan(recipe.payload["verification"]))
    if tamper == "remove_case":
        plan["cases"].pop()
    elif tamper == "relax_tolerance":
        plan["cases"][0]["metrics"]["out"]["absolute_tolerance"] = 100.0
    else:
        plan["cases"][0]["metrics"]["decision"]["expected"] = "negative"

    xrun = tmp_path / "xrun"
    calls = tmp_path / "xrun.calls"
    xrun.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{calls}'\nexit 0\n",
        encoding="utf-8",
    )
    xrun.chmod(0o755)
    context = ExecutorContext(
        recipe=recipe,
        run=run,
        gate_id="model_check",
        executor_name="xcelium.rnm_check",
        dependencies={
            "generate": ExecutorResult(
                "PASS",
                outputs={
                    "model_source": "missing-model.sv",
                    "smoke_testbench": "missing-tb.sv",
                    "pass_marker": "AIVW_RNM_SMOKE checks=5 PASS",
                    "l1_test_plan": plan,
                },
            )
        },
        environment={},
        tools={"xrun": str(xrun)},
        timeout=30,
    )
    context.gate_root.mkdir(parents=True)

    result = run_rnm_check(context)

    assert result.status == "BLOCKED_INPUT"
    assert result.summary == {
        "reason": "generation L1 test plan does not match the recipe"
    }
    assert not calls.exists()
    assert list(context.gate_root.iterdir()) == []


def test_xrun_version_requires_one_unambiguous_identity() -> None:
    assert _xrun_version("TOOL: xrun(64) 23.09-s002: Started") == "23.09-s002"
    assert _xrun_version("no identity") is None
    assert (
        _xrun_version(
            "TOOL: xrun 23.09-s002: Started",
            "TOOL: xrun(64) 23.09-s002: Exiting",
        )
        == "23.09-s002"
    )
    assert (
        _xrun_version(
            "TOOL: xrun 23.09-s002: Started",
            "TOOL: xrun 24.03-s001: Started",
        )
        is None
    )


def test_xcelium_evidence_binding_is_required_and_exact() -> None:
    plan = _plan()
    plan["coverage"] = {
        "id": "functional",
        "backend": "ucis",
        "minimum_score": 0.9,
        "required_points": [],
    }
    binding, findings = _validate_evidence_binding(plan, None, Path("/tmp/payload"))
    assert binding == {}
    assert {item["code"] for item in findings} == {"missing_evidence_binding"}

    binding, findings = _validate_evidence_binding(
        plan,
        {
            "coverage": {
                "id": "functional",
                "path": "evidence/coverage.ucis",
                "producer": "spectre",
            }
        },
        Path("/tmp/payload"),
    )
    assert binding["coverage"]["path"] == "evidence/coverage.ucis"
    assert "evidence_binding_producer_invalid" in {item["code"] for item in findings}

    mismatch = _compare_evidence_binding(
        binding,
        [{"event": "coverage", "id": "functional", "path": "evidence/other.ucis"}],
    )
    assert mismatch == [
        {
            "code": "evidence_binding_path_mismatch",
            "type": "coverage",
            "expected": "evidence/coverage.ucis",
            "actual": "evidence/other.ucis",
        }
    ]
