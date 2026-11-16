"""Opt-in actual IC231 process/IPC/TCP tests, with no model endpoint dependency."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from sico.adapters.virtuoso import context_tools
from sico.service.python_backend import PythonBackend
from sico.interpreter import cad_python
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from sico.transport.broker import ContextBroker

AI = Path(__file__).absolute().parents[2]
IC = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso")
pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_IC_TESTS") != "1" or not IC.is_file(),
    reason="Set SICO_IC_TESTS=1 for actual licensed IC23.10.130 integration",
)


def test_real_skill_relay_context_roundtrip(tmp_path):
    with SessionJournal(tmp_path, "ic231") as journal, ContextBroker(10) as broker:
        config = journal.directory / "connection.json"
        config.write_text(
            json.dumps(
                {
                    "host": broker.address[0],
                    "port": broker.address[1],
                    "token": broker.token,
                }
            )
        )
        config.chmod(0o600)
        command = shlex.join(
            [
                cad_python(),
                "-s",
                str(AI.parent / "sico/python/sico-entry"),
                "relay",
                "--connection-file",
                str(config),
            ]
        )
        stop = tmp_path / "stop"
        startup = tmp_path / "startup.il"
        lines = [
            f"load({json.dumps(str((AI.parent / 'sico' if name.startswith('SICO_') else AI) / 'skill' / name))})"
            for name in (
                "AI_protocol.il",
                "AI_eval.il",
                "SICO_context.il",
            )
        ]
        lines += [
            'printf("AGENT_VERSION %s\\n" getVersion(t))',
            'printf("AGENT_MAE_STATUS_CAPABILITY %L\\n" isCallable(\'maeCheckSimStatus))',
            'printf("AGENT_HISTORY_MENU_CAPABILITY %L\\n" '
            "isCallable('axlGetDataViewHistoryUserMenu))",
            f'sicoConnect({json.dumps(command)} "ic231-instance" "ic231-generation")',
            "agentProbeTicks=0",
            "procedure(agentProbeExit() agentProbeTicks=agentProbeTicks+1 "
            f"if(or(isFile({json.dumps(str(stop))}) agentProbeTicks>200) "
            'then exit() else hiRegTimer("agentProbeExit()" 2)))',
            'hiRegTimer("agentProbeExit()" 2)',
        ]
        startup.write_text("\n".join(lines) + "\n")
        environment = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("QT_")
            and k
            not in {
                "LD_LIBRARY_PATH",
                "LD_PRELOAD",
                "PYTHONHOME",
                "PYTHONPATH",
            }
        }
        log = tmp_path / "stdout.log"
        with log.open("w") as output:
            process = subprocess.Popen(
                [
                    str(IC),
                    "-nograph",
                    "-nocdsinit",
                    "-log",
                    str(tmp_path / "CDS.log"),
                    "-restore",
                    str(startup),
                ],
                cwd=tmp_path,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                try:
                    context = broker.context("ic231-instance", timeout=25)
                except TimeoutError:
                    pytest.fail(log.read_text())
                loop = PythonBackend(ContextDemoProvider(), context_tools(broker), journal, context)
                events = list(loop.run("读取本机 Virtuoso 上下文"))
                assert events[-1]["kind"] == "task.completed", events
                result = next(
                    e["payload"]["result"]["data"] for e in events if e["kind"] == "tool.finished"
                )
                assert result["valid"] is True
                assert "IC23.1" in result["version"]
                assert Path(result["cwd"]).samefile(tmp_path)
                assert not list(tmp_path.rglob("*spool*"))
            finally:
                stop.touch()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
        assert process.returncode == 0, log.read_text()
        assert "*Error*" not in log.read_text(), log.read_text()
