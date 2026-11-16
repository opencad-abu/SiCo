"""Isolated SKILL contract probes for the session failure fixes."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cadai.workflow_tools import build_workflow_skill

AI_ROOT = Path(__file__).parents[2]


def test_legacy_guards_private_truncate_and_deferred_route_checks(tmp_path):
    binary = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if not binary:
        pytest.skip("dbAccess unavailable")
    artifact = tmp_path / "snapshot.jsonl"
    os.close(os.open(artifact, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    guarded = build_workflow_skill(
        "run_maestro_simulation",
        dict(library="Work", cell="Tb", view="maestro"),
        guard_unavailable=True,
    )
    source = f"""
load({json.dumps(str(AI_ROOT / "skill/AI_circuit_create_check.il"))})
reviewResult={guarded}
unless(rexMatchp("capability_not_available" reviewResult) error("missing capability guard"))
printf("REVIEW_GUARD_ABSENT_PASS\\n")
procedure(aiMaestroRun(a b c d) "called-present-backend")
unless(equal({guarded} "called-present-backend") error("callable backend was blocked"))
printf("REVIEW_GUARD_PRESENT_PASS\\n")
reviewPort=outfile({json.dumps(str(artifact))} "w")
fprintf(reviewPort "private snapshot") close(reviewPort)
reviewEntry=makeTable("reviewEntry" nil)
reviewRecord=makeTable("reviewRecord" nil)
reviewEntry['targetAction]="continue" reviewEntry['targetRecord]=reviewRecord
reviewRecord['facts]=list(nil nil nil nil nil nil nil list(nil nil nil))
reviewRoute=list("VDD" nil nil list(list("inst" "M1" "B") list("inst" "M1" "S")
  list(1 0) list(0 1) 0.5 t))
reviewInst=list("M1" nil nil list(10 10) "R90" nil list(list("B" "VDD") list("S" "VDD"))
  nil list(list(-0.5 -0.5) list(0.5 0.5))
  list(list("B" list(list(0 0.5))) list("S" list(list(0 -0.5)))))
reviewEntry['payload]=list(list("probe" "cell" "schematic")
  list(reviewInst) nil list(reviewRoute) nil)
unless(aiCrAdditionCheck(reviewEntry) error("legal deferred route rejected"))
printf("REVIEW_ROUTE_PASS\\n")
reviewRecord['facts]=list(nil nil nil nil nil nil nil list(nil nil
  list(list("line" nil list(list(9.9 9.9) list(10.1 10.1))))))
when(errset(aiCrAdditionCheck(reviewEntry) t) error("overlap was accepted"))
printf("REVIEW_COLLISION_PASS\\n")
reviewRecord['facts]=list(nil nil nil nil nil nil nil list(nil nil nil))
reviewEntry['payload]=list(list("probe" "cell" "schematic") nil nil list(reviewRoute) nil)
when(errset(aiCrAdditionCheck(reviewEntry) t) error("missing route endpoint accepted"))
printf("REVIEW_MISSING_REF_PASS\\n")
reviewEntry['payload]=list(list("probe" "cell" "schematic") nil nil list(list("VDD" nil nil)) nil)
when(errset(aiCrAdditionCheck(reviewEntry) t) error("empty drawn wire accepted"))
printf("REVIEW_EMPTY_DRAW_PASS\\n")
reviewEntry['payload]=list(list("probe" "cell" "schematic") nil
  list(list("VDD" "input" list(0 0)) list("RETURN" "output" list(10 0)))
  list(list("VDD" nil nil list(list("port" "VDD") list("port" "VDD")
    list(1 0) list(1 0) 8 t))) nil)
reviewRecord['facts]=list(nil nil nil nil nil nil nil list(nil nil
  list(list("line" nil list(list(5 -0.1) list(5.1 0.1))))))
when(errset(aiCrAdditionCheck(reviewEntry) t) error("route-only overlap was accepted"))
printf("REVIEW_ROUTE_ONLY_COLLISION_PASS\\n")
load({json.dumps(str(AI_ROOT / "skill/AI_protocol.il"))})
load({json.dumps(str(AI_ROOT / "skill/AI_circuit.il"))})
load({json.dumps(str(AI_ROOT / "skill/AI_circuit_task.il"))})
load({json.dumps(str(AI_ROOT / "skill/AI_template_symbol.il"))})
aiCircuitTasks["task:review"]="background"
reviewMessage=aiTemplateSymbolPreview("task:review" "missing_library" "template" "digest"
  "reviewMissingLibrary" "Target" "sourceLib" "/source"
  list(list("IN" "input" 1 nil nil nil nil nil)) 2 2)
unless(and(rexMatchp("symbol_preview_failed" reviewMessage)
  rexMatchp("writable work library required" reviewMessage)) error("symbol cause lost"))
ddCreateLib("reviewWork" {json.dumps(str(tmp_path / "library"))})
reviewMessage=aiTemplateSymbolPreview("task:review" "missing_schematic" "template" "digest"
  "reviewWork" "Target" "sourceLib" "/source"
  list(list("IN" "input" 1 nil nil nil nil nil)) 2 2)
unless(and(rexMatchp("symbol_preview_failed" reviewMessage)
  rexMatchp("existing schematic required" reviewMessage)) error("symbol interface cause lost"))
printf("REVIEW_SYMBOL_CAUSE_PASS\\n")
exit()
"""
    result = subprocess.run(
        [binary, "-nocdsinit"],
        input=source,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=45,
    )
    output = result.stdout + result.stderr
    (tmp_path / "native-review.log").write_text(output)
    assert result.returncode == 0, output
    for marker in (
        "GUARD_ABSENT",
        "GUARD_PRESENT",
        "ROUTE",
        "COLLISION",
        "MISSING_REF",
        "EMPTY_DRAW",
        "ROUTE_ONLY_COLLISION",
        "SYMBOL_CAUSE",
    ):
        assert f"REVIEW_{marker}_PASS" in output, output
    assert "*Error* load:" not in output, output
    expected_errors = ("overlaps existing content", "instance/terminal/net differs",
                       "at least two planned points", "writable work library required",
                       "existing schematic required")
    errors = [line for line in result.stderr.splitlines() if "*Error*" in line]
    assert len(errors) == 6, output
    assert all(any(reason in line for reason in expected_errors) for line in errors), output
    assert "*WARNING*" not in output, output
    assert artifact.read_text() == "private snapshot"
    assert artifact.stat().st_mode & 0o777 == 0o600
