from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

AI_ROOT = Path(__file__).resolve().parents[2]
CAD_PYTHON = os.environ.get("CAD_PYTHON") or sys.executable


def test_ai_launch_form_is_compact_and_workspace_first() -> None:
    source = (AI_ROOT / "skill/AI_launcher.il").read_text(encoding="utf-8")
    form_body = source.split("procedure(aiCreateLaunchForm()", 1)[1].split(
        "procedure(cadDisplayAiAssistant()", 1
    )[0]

    assert form_body.index("workspace=hiCreateFileSelectorField") < form_body.index(
        "agent=hiCreateRadioField"
    )
    assert "?items list(list(workspace 'stretch 0) list(agent 'stretch 0))" in form_body
    assert '?choices list("Codex" "Claude")' in form_body
    assert '?value "Claude"' in form_body
    assert '?defValue "Claude"' in form_body
    assert "?spacing hiGetDefaultWidgetSpacing(?widgetsAreRelated t)" in form_body
    assert "?vert_align 'top" in form_body
    assert "?initialSize list(680 135)" in form_body
    assert "?minSize list(520 125)" in form_body


def _run_process_group(
    args: list[str], *, cwd: Path, env: dict[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
        raise
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_VIRTUOSO_PROBE") != "1",
    reason="set CAD_AI_RUN_VIRTUOSO_PROBE=1 to run the Virtuoso GUI probe",
)
def test_ai_native_launch_form(tmp_path: Path) -> None:
    virtuoso = shutil.which(os.environ.get("CAD_AI_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    if not os.environ.get("DISPLAY"):
        pytest.skip("the Virtuoso GUI probe requires DISPLAY")

    sources = [
        AI_ROOT / "skill/AI_protocol.il",
        AI_ROOT / "skill/AI_eval.il",
        AI_ROOT / "skill/AI_ipc.il",
        AI_ROOT / "skill/AI_launcher.il",
    ]
    lines = [f"load({json.dumps(str(path))})" for path in sources]
    lines.extend(
        (
            "cadAiLaunchForm=nil",
            "form=aiCreateLaunchForm()",
            "if(and(form form~>aiAgent form~>aiAgent~>value==\"Claude\" "
            "form~>aiWorkspace "
            'form~>aiWorkspace~>value==aiWorkingDirectory()) '
            'then printf("AI_VIRTUOSO_FORM_CREATE_OK\\n"))',
            "workspaceLocation=hiGetLayoutItemIndex(form 'aiWorkspace)",
            "agentLocation=hiGetLayoutItemIndex(form 'aiAgent)",
            "if(and(workspaceLocation==list('aiLaunchLayout 0) "
            "agentLocation==list('aiLaunchLayout 1)) "
            'then printf("AI_VIRTUOSO_FORM_ORDER_OK\\n"))',
            "aiProbeAgent=nil",
            "procedure(aiStartAgent(workspace agent) aiProbeAgent=agent nil)",
            'form~>aiAgent~>value="Claude"',
            "aiLaunchFormCB(form)",
            'if(aiProbeAgent=="claude" '
            'then printf("AI_VIRTUOSO_CLAUDE_SELECT_OK\\n"))',
            "cadDisplayAiAssistant()",
            "if(and(cadAiLaunchForm hiIsFormDisplayed(cadAiLaunchForm)) "
            'then printf("AI_VIRTUOSO_FORM_DISPLAY_OK\\n"))',
            "when(cadAiLaunchForm hiFormClose(cadAiLaunchForm))",
            "exit()",
        )
    )
    restore = tmp_path / "ai-form-probe.il"
    restore.write_text("\n".join(lines), encoding="utf-8")
    completed = _run_process_group(
        [
            virtuoso,
            "-nographE",
            "-nocdsinit",
            "-log",
            str(tmp_path / "CDS.log"),
            "-restore",
            str(restore),
        ],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=90,
    )
    output = completed.stdout + completed.stderr
    log = (tmp_path / "CDS.log").read_text(encoding="utf-8", errors="replace")
    combined = output + log
    assert completed.returncode == 0, combined
    assert "AI_VIRTUOSO_FORM_CREATE_OK" in combined
    assert "AI_VIRTUOSO_FORM_ORDER_OK" in combined
    assert "AI_VIRTUOSO_CLAUDE_SELECT_OK" in combined
    assert "AI_VIRTUOSO_FORM_DISPLAY_OK" in combined


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_FULL_PROBE") != "1",
    reason="set CAD_AI_RUN_FULL_PROBE=1 to run the full Virtuoso session probe",
)
def test_full_virtuoso_controller_terminal_mcp_session(tmp_path: Path) -> None:
    virtuoso = shutil.which(os.environ.get("CAD_AI_VIRTUOSO", "virtuoso"))
    terminal_text = os.environ.get("CAD_AI_TERMINAL", "")
    terminal = Path(terminal_text) if terminal_text else None
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    if terminal is None or not terminal.is_file():
        pytest.skip("CAD_AI_TERMINAL must name the installed Qt5 terminal")
    if not os.environ.get("DISPLAY"):
        pytest.skip("the full Virtuoso probe requires DISPLAY")

    install_root = terminal.resolve().parent.parent
    installed_entry = install_root / "python/sico-ai"
    entry = installed_entry if installed_entry.is_file() else AI_ROOT / "python/sico-ai"
    installed_skill = install_root / "skill"
    skill_root = (
        installed_skill
        if (installed_skill / "AI_protocol.il").is_file()
        else AI_ROOT / "skill"
    )
    marker = tmp_path / "mcp-call.ok"
    screenshot = tmp_path / "terminal.png"
    runtime_base = tmp_path / "runtime"
    runtime_base.mkdir(mode=0o700)
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "\n".join(
            (
                f"#!{CAD_PYTHON}",
                "import json",
                "import subprocess",
                "import sys",
                "import time",
                "from pathlib import Path",
                "if sys.argv[1:] == ['--version']:",
                "    print('codex-cli 0.162.1')",
                "    raise SystemExit(0)",
                f"entry = {str(entry)!r}",
                f"marker = {str(marker)!r}",
                f"screenshot = {str(screenshot)!r}",
                "process = subprocess.Popen(",
                f"    [{CAD_PYTHON!r}, entry, 'mcp', '--request-timeout', '15'],",
                "    stdin=subprocess.PIPE, stdout=subprocess.PIPE)",
                "def exchange(message):",
                "    process.stdin.write((json.dumps(message) + '\\n').encode())",
                "    process.stdin.flush()",
                "    return json.loads(process.stdout.readline())",
                "initialized = exchange({'jsonrpc': '2.0', 'id': 1, "
                "'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}})",
                "process.stdin.write(b'{\"jsonrpc\":\"2.0\",\"method\":'"
                " b'\"notifications/initialized\"}\\n')",
                "process.stdin.flush()",
                "called = exchange({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', "
                "'params': {'name': 'get_context', 'arguments': {}}})",
                "payload = json.loads(called['result']['content'][0]['text'])",
                "if initialized['result']['serverInfo']['name'] == 'sico_ic' "
                "and called['result']['isError'] is False and payload['ok'] is True:",
                "    with open(marker, 'w', encoding='ascii') as handle:",
                "        handle.write('AI_FULL_MCP_OK\\n')",
                "    print('AI_FULL_MCP_OK', flush=True)",
                "deadline = time.monotonic() + 10",
                "while not Path(screenshot).is_file():",
                "    if time.monotonic() >= deadline:",
                "        raise SystemExit('terminal screenshot timed out')",
                "    time.sleep(0.05)",
                "process.stdin.close()",
                "raise SystemExit(process.wait())",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    sources = [
        skill_root / "AI_protocol.il",
        skill_root / "AI_eval.il",
        skill_root / "AI_ipc.il",
        skill_root / "AI_launcher.il",
    ]
    lines = [f"load({json.dumps(str(path))})" for path in sources]
    lines.extend(
        (
            "aiFullProbeTicks=0",
            "procedure(aiFullProbeWait()",
            "  aiFullProbeTicks=aiFullProbeTicks+1",
            "  cond(",
            "    ((!aiIpcState['cid])",
            '      printf("AI_FULL_SESSION_EXIT_OK\\n") exit())',
            "    ((aiFullProbeTicks>150)",
            "      aiStop()",
            '      printf("AI_FULL_SESSION_TIMEOUT\\n") exit())',
            '    (t hiRegTimer("aiFullProbeWait()" 2)))',
            ")",
            f"cid=aiStart({json.dumps(str(tmp_path))})",
            "unless(cid printf(\"AI_FULL_SESSION_START_FAILED\\n\") exit())",
            'when(cid hiRegTimer("aiFullProbeWait()" 2))',
        )
    )
    restore = tmp_path / "ai-full-probe.il"
    restore.write_text("\n".join(lines), encoding="utf-8")
    env = os.environ.copy()
    env.pop("CAD_HOME", None)
    env.update(
        {
            "CAD_CODEX_CLI": str(fake_codex),
            "CAD_CODEX_HOME": str(tmp_path / "codex-home"),
            "CAD_AI_PYTHON_ENTRY": str(entry),
            "CAD_AI_REQUEST_TIMEOUT": "15",
            "CAD_AI_SYSTEM_LD_LIBRARY_PATH": os.environ.get(
                "CAD_AI_SYSTEM_LD_LIBRARY_PATH", ""
            ),
            "CAD_AI_QT_PLUGIN_PATH": os.environ.get("CAD_AI_QT_PLUGIN_PATH", ""),
            "CAD_AI_TERMINAL": str(terminal),
            "CAD_AI_TEST_EXIT_ON_SESSION_FINISH": "1",
            "CAD_AI_TEST_SCREENSHOT": str(screenshot),
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_RUNTIME_DIR": str(runtime_base),
        }
    )
    completed = _run_process_group(
        [
            virtuoso,
            "-nographE",
            "-nocdsinit",
            "-log",
            str(tmp_path / "CDS.log"),
            "-restore",
            str(restore),
        ],
        cwd=tmp_path,
        env=env,
        timeout=60,
    )
    log = (tmp_path / "CDS.log").read_text(encoding="utf-8", errors="replace")
    combined = completed.stdout + completed.stderr + log
    assert completed.returncode == 0, combined
    assert "AI_FULL_SESSION_EXIT_OK" in combined
    assert "AI Assistant terminal exited with status 0" in combined
    assert "Fatal Python error" not in combined
    assert marker.read_text(encoding="ascii") == "AI_FULL_MCP_OK\n"
    assert screenshot.stat().st_size > 1_000
    assert not list(runtime_base.glob("cad-ai-*"))
