"""Owned visible acceptance session. Normal pytest uses the disposable GUI fixture."""

import json
import os
import subprocess
from contextlib import contextmanager

from circuit_create_support import AI_ROOT, IC_LIBS, MODULES, DispatchClient, NativeBridge, session

from cadai.controller import RequestDispatcher
from cadai.mcp import McpServer
from cadai.runtime import RuntimePaths


@contextmanager
def migration_session(path):
    display = os.environ.get("CAD_AI_TEMPLATE_FOREGROUND_DISPLAY")
    if not display:
        with session(path) as opened:
            yield opened
        return
    path.mkdir(parents=True, exist_ok=True)
    (path / "cds.lib").write_text(
        f"DEFINE analogLib {IC_LIBS}/artist/analogLib\nDEFINE basic {IC_LIBS}/basic\n"
    )
    lines = ["load(" + json.dumps(str(AI_ROOT / "skill" / m)) + ")" for m in MODULES]
    lines += [
        'procedure(templateMigrationPoll() when(isFile("command.il") load("command.il") '
        'deleteFile("command.il")) hiRegTimer("templateMigrationPoll()" 1))',
        "templateMigrationPoll()",
        'aiWriteTextFile("ready" "yes")',
    ]
    (path / "run.il").write_text("\n".join(lines) + "\n")
    with (path / "process.log").open("w") as log:
        proc = subprocess.Popen(
            ["virtuoso", "-nocdsinit", "-norestore", "-replay", "run.il", "-log", "CDS.log"],
            cwd=path,
            env=dict(os.environ, DISPLAY=display),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (path / "pid").write_text(str(proc.pid))
    bridge = NativeBridge(path, proc)
    bridge.wait_file(path / "ready")
    (path / "spool").mkdir(mode=0o700)
    server = McpServer(
        DispatchClient(
            RequestDispatcher(bridge, RuntimePaths(path, path / "socket", path / "spool"), path)
        ),
        workspace=path,
    )
    try:
        yield bridge, server, proc
    finally:
        if proc.poll() is None:
            bridge.skill("progn(procedure(templateMigrationPoll() t) t)")
            (path / "polling-disabled").write_text(
                "Owned foreground retained; temporary polling disabled.\n"
            )
