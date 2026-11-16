"""Use the installed Cadence reader as evidence for tricky lexical cases."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from cadai.skill_check import check_source


def test_preflight_lexical_cases_agree_with_cadence_reader(tmp_path):
    executable = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if executable is None:
        pytest.skip("dbAccess is unavailable")
    cases = [
        # All are complete single forms; readstring is not used as a file validator.
        ("list(1 ; continued " + chr(92) + "\n ) ]\n 2)", True, True),
        ("list(1 ; continued " + chr(92) * 2 + "\n ) ]\n 2)", True, True),
        ('/* nested-looking /* ends here */ list("( [")', True, True),
        (r"list(\050 \051 \133 \135 \;)", True, True),
        ("procedure(doubleValue(x) x*2)", True, True),
        ("'(defun f (x) x)", True, True),
        ("list(table[fun(x)])", True, True),
        # Some Cadence reader configurations warn and silently supply the
        # missing delimiter. Our gate must reject these regardless of recovery.
        ("list(1", None, False),
        ('"unfinished', None, False),
        # Native-valid syntax deliberately excluded by AI Assistant policy.
        ("list(list(1]", True, False),
        ("defun(f (x) x)", True, False),
        ("(defun f (x) x)", True, False),
        # lambda/prog/let 现在是允许的片段形式；Lisp 写法的 lambda 仍被拒绝。
        ("lambda((x) x)", True, True),
        ("(lambda (x) x)", True, False),
    ]
    commands = []
    for index, (source, native_valid, allowed) in enumerate(cases):
        report = check_source(source.encode())
        assert report["ok"] is allowed, report
        commands.append(
            f'printf("PREFLIGHT_READER_{index} %s\\n" '
            f'if(errset(readstring({json.dumps(source)}) nil) then "pass" else "fail"))'
        )
    commands.append("exit()")
    completed = subprocess.run(
        [executable],
        input="\n".join(commands) + "\n",
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    for index, (_source, native_valid, _allowed) in enumerate(cases):
        if native_valid is None:
            assert f"PREFLIGHT_READER_{index} " in output, output
            continue
        expected = "pass" if native_valid else "fail"
        assert f"PREFLIGHT_READER_{index} {expected}" in output, output
