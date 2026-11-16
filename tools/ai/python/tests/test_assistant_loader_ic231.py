"""Assistant and Copilot source entries share one process without reloading code."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[3]
DB = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess")
LOADERS = {"assistant": TOOLS / "ai/skill++/AI.ils",
           "copilot": TOOLS / "sico/skill++/SICO.ils"}


def run_probe(tmp_path, lines, installation=TOOLS.parent, gui=False, expected_error=None):
    source = tmp_path / "loader.il"
    source.write_text("\n".join(lines + ['printf("LOADER_PASS\\n") exit()']) + "\n")
    env = {key: value for key, value in os.environ.items()
           if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "CAD_HOME"}
           and not key.startswith("SICO_")}
    env["SICO_HOME"] = str(installation)
    command = ([str(DB.with_name("virtuoso")), "-nographE", "-nocdsinit",
                "-log", str(tmp_path / "CDS.log"), "-restore", str(source)]
               if gui else [str(DB), "-load", str(source)])
    result = subprocess.run(command, cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    combined = result.stdout + result.stderr
    (tmp_path / "stdout.log").write_text(combined)
    assert result.returncode == 0 and "LOADER_PASS" in combined, combined
    if expected_error:
        assert expected_error in combined, combined
    else:
        assert "*Error*" not in combined, combined
    return combined


@pytest.mark.skipif(not DB.is_file(), reason="IC231 dbAccess is not installed")
@pytest.mark.parametrize("order", [
    ["assistant", "assistant"], ["copilot", "assistant"], ["assistant", "copilot"],
])
def test_entries_preserve_definitions_and_state(tmp_path, order):
    loads = [f"load({json.dumps(str(LOADERS[name]))})" for name in order]
    combined = run_probe(tmp_path, [
        loads[0],
        "loaderPdk=getd('aiPdkCapture) loaderWindow=getd('aiWindowInspect)",
        "loaderCreate=getd('aiCreateExecute)",
        loads[1],
        'unless(and(eq(loaderPdk getd(\'aiPdkCapture))'
        ' eq(loaderWindow getd(\'aiWindowInspect))'
        ' eq(loaderCreate getd(\'aiCreateExecute))) error("Shared definitions replaced"))',
        "loaderIpc=aiIpcState",
        "loaderResolve=getd('aiResolveRoot) loaderFrontend=getd('aiLoadFrontend)",
        "aiIpcState['loaderProbe]=list(\"preserved\")",
        *loads, *loads,
        'unless(and(boundp(\'aiLoaderVersion) aiLoaderVersion==aiLoaderRevision()'
        ' eq(loaderResolve getd(\'aiResolveRoot)) eq(loaderFrontend getd(\'aiLoadFrontend))'
        ' eq(loaderIpc aiIpcState) aiIpcState[\'loaderProbe]==list("preserved"))'
        ' error("Assistant completion or state changed"))',
    ])
    assert "redefined" not in combined.lower(), combined


@pytest.mark.skipif(not DB.is_file(), reason="IC231 dbAccess is not installed")
@pytest.mark.parametrize("missing", ["AI_launcher.il", "AI_pdk_core.il"])
def test_incomplete_load_can_be_retried(tmp_path, missing):
    installation = tmp_path / "installation"
    for directory in ["tools/ai/skill", "tools/ai/skill++", "tools/common/skill", "etc/config"]:
        shutil.copytree(TOOLS.parent / directory, installation / directory)
    shutil.copytree(TOOLS.parent / "scripts", installation / "scripts")
    (installation / "bin").mkdir()
    target = installation / "tools/ai/skill" / missing
    held = target.with_suffix(".held")
    target.rename(held)
    loader = json.dumps(str(installation / "tools/ai/skill++/AI.ils"))
    restore = json.dumps(f"cp {shlex.quote(str(held))} {shlex.quote(str(target))}")
    combined = run_probe(tmp_path, [
        f"when(errset(load({loader}) nil) error(\"Incomplete load accepted\"))",
        'when(and(boundp(\'aiLoaderVersion) aiLoaderVersion) error("False completion"))',
        f"unless(system({restore})==0 error(\"Restore failed\"))",
        f"load({loader})",
        'unless(aiLoaderVersion==aiLoaderRevision() error("Retry did not complete"))',
        'printf("RETRY_COMPLETE\\n")', f"load({loader})",
    ], installation=installation, expected_error=(
        "SiCo source load failed" if missing == "AI_launcher.il"
        else "load: can't access file"))
    assert "redefined" not in combined.split("RETRY_COMPLETE")[-1].lower(), combined


@pytest.mark.skipif(not DB.with_name("virtuoso").is_file(), reason="IC231 not installed")
def test_virtuoso_repeated_assistant_form_keeps_loaded_functions(tmp_path):
    loader = json.dumps(str(LOADERS["assistant"]))
    combined = run_probe(tmp_path, [
        f"load({json.dumps(str(LOADERS['copilot']))})", f"load({loader})",
        "loaderLaunch=getd('cadDisplayAiAssistant)",
        f"for(loaderRepeat 1 3 load({loader}) cadDisplayAiAssistant()",
        ' unless(and(cadAiLaunchForm hiIsFormDisplayed(cadAiLaunchForm))'
        ' error("Assistant form not displayed")) hiFormClose(cadAiLaunchForm))',
        'unless(eq(loaderLaunch getd(\'cadDisplayAiAssistant)) error("Launcher replaced"))',
    ], gui=True)
    assert "redefined" not in combined.lower(), combined


@pytest.mark.skipif(not DB.is_file(), reason="IC231 dbAccess is not installed")
def test_assistant_guard_uses_skill_value_slot_after_legacy_load(tmp_path):
    legacy = tmp_path / "legacy.ils"
    legacy.write_text('aiLoaderVersion="legacy"\nset(\'aiLoaderVersion "legacy")\n')
    loader = json.dumps(str(LOADERS["assistant"]))
    combined = run_probe(tmp_path, [
        f"load({json.dumps(str(legacy))})", f"load({loader})",
        f"for(loaderRepeat 1 3 load({loader}))",
        'unless(aiLoaderVersion==aiLoaderRevision() error("Stale completion marker"))',
    ])
    assert "redefined" not in combined.lower(), combined
