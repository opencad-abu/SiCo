"""Translate private environment aliases only for the selected STDIO child."""

import json
import os
import sys


def main():
    try:
        value = json.loads(sys.argv[1])
        environment = {key: item for key, item in os.environ.items()
                       if not key.startswith("SICO_") and key not in {"PYTHONPATH", "PYTHONHOME"}}
        environment.update({key: os.environ[alias] for key, alias in value["refs"].items()})
        os.execvpe(value["command"], [value["command"], *value["args"]], environment)
    except (KeyError, ValueError, OSError, IndexError):
        # Never expose referenced values through stderr/native diagnostics.
        raise SystemExit("External MCP could not start") from None
