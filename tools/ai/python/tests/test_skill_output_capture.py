"""Native acceptance of synchronous output capture and request isolation."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cadai.skill_check import check_source

AI = Path(__file__).resolve().parents[2]


def run_native(tmp_path, body, files=None):
    executable = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if not executable:
        pytest.skip("dbAccess unavailable")
    for name, text in (files or {}).items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "cds.lib").write_text("")
    lines = []
    for name in ("AI_protocol.il", "AI_eval.il"):
        source = AI / "skill" / name
        assert check_source(source.read_bytes(), source_name=name)["ok"]
        lines.append(f"load({json.dumps(str(source))})")
    # One protected form makes SKILL assertion failures fatal to the test.
    lines += ["unless(errset(progn(", *body,
              'aiWriteTextFile("passed" "yes")) t) exit(1))', "exit()"]
    script = tmp_path / "probe.il"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONPATH", "PYTHONHOME"}}
    completed = subprocess.run(
        [executable, "-nocdsinit", "-cdslib", str(tmp_path / "cds.lib"),
         "-load", str(script)], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30,
    )
    output = completed.stdout + completed.stderr
    (tmp_path / "native.log").write_text(output)
    assert completed.returncode == 0 and (tmp_path / "passed").exists(), output
    return output


@pytest.mark.parametrize("method", ["eval", "il", "ils"])
@pytest.mark.parametrize("failure", [False, True])
def test_capture_all_ports_and_last_warning_restores_originals(tmp_path, method, failure):
    expression = (
        'progn(printf("PRINT\\n") warn("FIRST\\n") warn("LAST\\n") '
        'fprintf(woport "WARNING_PORT\\n") fprintf(errport "ERROR_PORT\\n") '
        + ('error("EXPECTED_FAILURE")' if failure else "nil") + ")"
    )
    # SKILL++ uses importSkillVar for the SKILL value-slot ports.
    prefix = "importSkillVar(woport errport)\n" if method == "ils" else ""
    source = "source." + ("il" if method == "eval" else method)
    evaluator = "aiEvalExpression" if method == "eval" else "aiLoadSkillFile"
    run_native(tmp_path, [
        "ocPo=outstring() ocWo=outstring() ocErr=outstring()",
        "let(((poport ocPo) (woport ocWo) (errport ocErr))",
        '  warn("BEFORE_REQUEST\\n")',
        f'  {evaluator}("{source}" "result.json")',
        '  unless(and(poport==ocPo woport==ocWo errport==ocErr '
        '    openportp(ocPo) openportp(ocWo) openportp(ocErr)) error("Ports not restored"))',
        '  unless(!getWarn() error("Warning escaped request"))',
        '  printf("AFTER_REQUEST\\n")',
        '  aiEvalExpression("next.il" "next.json"))',
        'aiWriteTextFile("outer-output" getOutstring(ocPo))',
        'aiWriteTextFile("outer-warning" getOutstring(ocWo))',
        'aiWriteTextFile("outer-error" getOutstring(ocErr))',
        "close(ocPo) close(ocWo) close(ocErr)",
    ], {source: prefix + expression, "next.il": "42"})
    reply = json.loads((tmp_path / "result.json").read_text())
    assert reply["ok"] is not failure
    assert reply["output_capture"] == "skill_ports"
    assert reply["output"].startswith("PRINT\n*WARNING* FIRST\nWARNING_PORT\nERROR_PORT\n")
    assert reply["output"].endswith("*WARNING* LAST\n")
    assert not reply["output_truncated"]
    if failure:
        assert ("EXPECTED_FAILURE" if method == "eval" else "error while loading file") in reply["error"]
        assert "*Error* EXPECTED_FAILURE" in reply["output"]
    else:
        assert reply["value"] == ("nil" if method == "eval" else "t")
    assert "BEFORE_REQUEST" not in reply["output"]
    assert "AFTER_REQUEST" not in reply["output"]
    assert (tmp_path / "outer-output").read_text() == "AFTER_REQUEST\n"
    assert (tmp_path / "outer-warning").read_text() == "*WARNING* BEFORE_REQUEST\n"
    assert (tmp_path / "outer-error").read_text() == ""
    following = json.loads((tmp_path / "next.json").read_text())
    assert following["value"] == "42" and following["output"] == ""


def test_nested_capture_and_rejected_throw_close_only_owned_port(tmp_path):
    run_native(tmp_path, [
        "ocOuter=outstring()",
        "let(((poport ocOuter) (woport ocOuter) (errport ocOuter))",
        '  aiEvalExpression("outer.il" "outer.json")',
        '  ocThrown=catch(\'ocTag aiEvalExpression("throw.il" "throw.json"))',
        '  unless(and(!ocThrown !openportp(ocCaptured) '
        '    poport==ocOuter woport==ocOuter errport==ocOuter openportp(ocOuter))',
        '    error("Failed throw did not restore ports"))',
        '  printf("AFTER_THROW\\n"))',
        'aiWriteTextFile("after" getOutstring(ocOuter)) close(ocOuter)',
    ], {
        "outer.il": 'progn(printf("OUTER\\n") warn("OUTER_WARN\\n") '
                    'aiEvalExpression("inner.il" "inner.json") printf("RESUMED\\n") nil)',
        "inner.il": 'progn(printf("INNER\\n") warn("INNER_WARN\\n") nil)',
        "throw.il": "progn(ocCaptured=poport throw('ocTag 17))",
    })
    outer = json.loads((tmp_path / "outer.json").read_text())
    inner = json.loads((tmp_path / "inner.json").read_text())
    assert outer["output"] == "OUTER\n*WARNING* OUTER_WARN\nRESUMED\n"
    assert inner["output"] == "INNER\n*WARNING* INNER_WARN\n"
    assert (tmp_path / "after").read_text() == "AFTER_THROW\n"
    # errsetstring starts its own top level; throw cannot cross that boundary.
    rejected = json.loads((tmp_path / "throw.json").read_text())
    assert not rejected["ok"] and "throw" in rejected["error"]


@pytest.mark.parametrize("chunk", ["中文🙂", "\n\t\x01\\\""])
def test_large_output_and_value_fit_sico_reply_without_broken_utf8(tmp_path, chunk):
    # Use a file literal because SKILL does not interpret JSON \u escapes.
    literal = '"' + chunk.replace("\\", "\\\\").replace('"', '\\"').replace(
        "\n", "\\n").replace("\t", "\\t").replace("\x01", "\\001") + '"'
    expression = (f"let(((longText {literal})) "
                  "for(longIndex 1 14 longText=strcat(longText longText)) "
                  'printf("%s" longText) longText)')
    run_native(tmp_path, ['aiEvalExpression("long.il" "result.json")'], {"long.il": expression})
    raw = (tmp_path / "result.json").read_bytes()
    reply = json.loads(raw.decode("utf-8"))
    assert reply["ok"] and reply["output_truncated"] and reply["value_truncated"]
    expected = chunk * 16384
    for key in ("value", "output"):
        assert reply[key] and expected.startswith(reply[key])
        assert len(reply[key].encode()) <= 65536
    assert len(raw) < 180000
