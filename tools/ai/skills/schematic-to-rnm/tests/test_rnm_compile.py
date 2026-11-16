"""Optional compiler behavior with deterministic process doubles, no EDA launch."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_rnm
import rnm_compile


def test_unavailable_compiler_does_not_create_scratch(monkeypatch):
    monkeypatch.setattr(rnm_compile.shutil, "which", lambda name: None)

    def unexpected_scratch(**kwargs):
        pytest.fail("unavailable compiler must not allocate scratch")

    monkeypatch.setattr(rnm_compile.tempfile, "TemporaryDirectory", unexpected_scratch)
    assert check_rnm.compile_check(Path("model.sv"), []) == [
        check_rnm.Finding(
            "warning", "compile", "model.sv", "xrun is not on PATH; syntax was not checked"
        )
    ]


@pytest.mark.parametrize(
    "returncode,stdout,stderr,expected",
    [
        (0, "", "", [("info", "xrun -compile succeeded")]),
        (9, "plain failure", "", [("error", "xrun -compile exited 9")]),
        (0, "*W,PORT: questionable\n*W,PORT: questionable", "",
         [("warning", "*W,PORT: questionable")]),
        (1, "*E,PARSE: syntax\n*E,PARSE: syntax", "*F,FATAL: stopped",
         [("error", "*E,PARSE: syntax"), ("error", "*F,FATAL: stopped")]),
        (0, "*E,DLCPTH: stale cds.lib\n*W,NOTOPL: no top", "",
         [("info", "xrun -compile succeeded")]),
        (1, "*E,DLCPTH: stale cds.lib", "", [("error", "xrun -compile exited 1")]),
    ],
)
def test_compile_diagnostics_argv_and_cleanup(
    tmp_path, monkeypatch, returncode, stdout, stderr, expected
):
    source = tmp_path / "candidate.sv"
    source.write_text("read-only candidate")
    before = source.read_bytes()
    scratches = []
    monkeypatch.setattr(rnm_compile.shutil, "which", lambda name: "/fake/xrun")

    def run(command, **kwargs):
        scratch = Path(kwargs["cwd"])
        scratches.append(scratch)
        assert scratch.is_dir()
        assert command == [
            "/fake/xrun", "-compile", "-sv", "-nolog", "-xmlibdirpath",
            str(scratch), "-ams", "-define", "VALUE=1", str(source),
        ]
        assert kwargs == {
            "cwd": str(scratch), "capture_output": True, "text": True, "timeout": 300
        }
        (scratch / "compiler-output").write_text("temporary")
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(rnm_compile.subprocess, "run", run)
    findings = check_rnm.compile_check(source, iter(["-ams", "-define", "VALUE=1"]))
    assert findings == [
        check_rnm.Finding(severity, "compile", source.name, message)
        for severity, message in expected
    ]
    assert len(scratches) == 1 and not scratches[0].exists()
    assert source.read_bytes() == before


def test_ams_include_failure_suppresses_only_its_cascade(tmp_path, monkeypatch):
    monkeypatch.setattr(rnm_compile.shutil, "which", lambda name: "/fake/xrun")
    output = "\n".join([
        '*E,COFILX: cannot open disciplines.vams',
        '*E,DUPIDN: duplicate', '*E,NOIPRT: port', '*E,VLGERR: parse',
        '*E,SVNOTY: unknown type', '*E,OTHER: independent',
    ])
    monkeypatch.setattr(
        rnm_compile.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, output, ""),
    )
    findings = check_rnm.compile_check(tmp_path / "model.sv", [])
    assert len(findings) == 2
    assert findings[0] == check_rnm.Finding(
        "error", "compile", "model.sv", "*E,OTHER: independent"
    )
    assert findings[1].severity == "warning"
    assert "syntax was not checked in pure SystemVerilog mode" in findings[1].message


@pytest.mark.parametrize("failure", ["timeout", "oserror", "interrupt"])
def test_compiler_failure_cleans_scratch(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(rnm_compile.shutil, "which", lambda name: "/fake/xrun")
    scratches = []

    def run(command, **kwargs):
        scratches.append(Path(kwargs["cwd"]))
        assert scratches[-1].is_dir()
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 300)
        if failure == "oserror":
            raise OSError("launch denied")
        raise KeyboardInterrupt

    monkeypatch.setattr(rnm_compile.subprocess, "run", run)
    if failure == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            check_rnm.compile_check(tmp_path / "model.sv", [])
    else:
        findings = check_rnm.compile_check(tmp_path / "model.sv", [])
        expected = "xrun -compile timed out" if failure == "timeout" else "cannot run xrun: launch denied"
        assert findings == [check_rnm.Finding("warning", "compile", "model.sv", expected)]
    assert len(scratches) == 1 and not scratches[0].exists()
