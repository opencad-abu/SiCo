"""Bounded data protocol for SKILL callers of the shared project state owner."""

from __future__ import annotations

import argparse
import os
import shlex
import sys

from cadenv import EDA_TEMP_ENV_NAMES, EDA_TEMP_MARKERS
from sicotemp import initialize, initialize_project

FORMAT = "sico.state.environment.v1"
ERROR_FORMAT = "sico.state.error.v1"
ENVIRONMENT_KEYS = ("SICO_TEMP_DIR", "PYTHONDONTWRITEBYTECODE",
                    *EDA_TEMP_ENV_NAMES, *EDA_TEMP_MARKERS)


def _skill_string(value):
    if any(ord(char) < 32 and char not in "\n\t\r" for char in value):
        raise ValueError("State response contains unsupported control characters")
    return '"' + (value.replace("\\", "\\\\").replace('"', '\\"')
                  .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")) + '"'


def response(launch, *, scope="project", state=None, environment=None):
    target = dict(os.environ if environment is None else environment)
    initialize_scope = initialize if scope == "ai" else initialize_project
    directory = initialize_scope(target, cwd=launch, temporary=state)
    root = directory.parent if scope == "ai" else directory
    # A nested env command is valid even when the caller has already supplied
    # assignments to its outer env. Unset both root and marker families first.
    unset = ("SICO_TEMP_DIR", "CAD_TEMP_DIR", *EDA_TEMP_MARKERS)
    prefix = "env " + " ".join("-u " + name for name in unset) + " "
    prefix += " ".join(name + "=" + shlex.quote(target[name])
                       for name in ENVIRONMENT_KEYS if name in target) + " "
    values = (FORMAT, str(root), str(directory), prefix)
    result = "(" + " ".join(map(_skill_string, values)) + ")"
    if len(result.encode("utf-8")) > 65536:
        raise ValueError("State response exceeds the SKILL protocol limit")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sico-state")
    parser.add_argument("--launch", required=True)
    parser.add_argument("--scope", choices=("project", "ai"), default="project")
    parser.add_argument("--state")
    parser.add_argument("--error-format", choices=("text", "skill"), default="text")
    args = parser.parse_args(argv)
    try:
        print(response(args.launch, scope=args.scope, state=args.state))
    except (OSError, ValueError) as exc:
        # SKILL IPC stderr callbacks can be deferred until the evaluation ends.
        # Return bounded error data on stdout too; never require that callback
        # to diagnose a synchronous menu invocation.
        message = "".join(char if ord(char) >= 32 and ord(char) != 127 else " "
                          for char in str(exc))[:2048]
        if args.error_format == "skill":
            print("(" + _skill_string(ERROR_FORMAT) + " " + _skill_string(message) + ")")
        else:
            print("SiCo state: " + message, file=sys.stderr)
        return 2
    return 0
