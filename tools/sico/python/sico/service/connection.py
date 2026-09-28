"""Prepare the private relay descriptor and context-only CIW invocation."""

from sicoenv import read as environment_setting
import json
import os
from pathlib import Path
import shlex

from ..interpreter import agent_command
from ..installation import current
from ..storage.journal import open_private


def context_load_forms(context_file=None, *, environment=None):
    """Select a declared context or require the site's already loaded runtime."""
    environment = os.environ if environment is None else environment
    configured = context_file or environment_setting(environment, "SICO_AI_CONTEXT", "").strip()
    if context_file is None and "SICO_AI_CONTEXT" in environment and not configured:
        raise ValueError("SICO_AI_CONTEXT must not be empty")
    installed = current(environment).tool("ai") / "context/cadAiRunCtx.cxt"
    selected = Path(configured).expanduser().absolute() if configured else installed
    if configured or selected.exists():
        if selected.suffix != ".cxt" or not selected.is_file():
            raise ValueError("CAD AI context must be an available .cxt file: " + str(selected))
        return [
            f'unless(loadContext({json.dumps(str(selected))} t) '
            'error("CAD AI context failed to load"))',
            f'unless(callInitProc({json.dumps(selected.stem)}) '
            'error("CAD AI context initialization failed"))',
        ]
    return []


def connection_instructions(journal, broker, instance, generation, *, context_file=None,
                            environment=None):
    forms = context_load_forms(context_file, environment=environment)
    config_path = journal.directory / "connection.json"
    command = shlex.join(agent_command("relay", "--connection-file", str(config_path)))
    fd = open_private(config_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
    with os.fdopen(fd, "w") as stream:
        json.dump({"host": broker.address[0], "port": broker.address[1], "token": broker.token},
                  stream)
    forms.extend([
        "unless(isCallable('sicoConnect) "
        'error("SiCo AI context is not loaded; configure SICO_AI_CONTEXT or --context-file"))',
        f"sicoConnect({json.dumps(command)} {json.dumps(instance)} "
        f"{json.dumps(generation)})",
    ])
    # One form prevents a failed context load from falling through to a stale relay.
    return "Execute in the target Virtuoso CIW:\nprogn(\n  " + "\n  ".join(forms) + "\n)"
