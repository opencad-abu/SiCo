"""Record project modules actually loaded by one source-test process."""

import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(os.environ["SICO_SOURCE_TEST_ROOT"]).resolve()
OUTPUT = Path(os.environ["SICO_SOURCE_TEST_REPORT"])
PREFIXES = set(json.loads(os.environ["SICO_SOURCE_TEST_PREFIXES"]))


def pytest_sessionfinish(session, exitstatus):
    loaded, errors = [], []
    for name, module in list(sys.modules.items()):
        location = getattr(module, "__file__", None)
        if not location:
            continue
        path = Path(location).resolve()
        if path.is_relative_to(ROOT):
            if path.suffix != ".py":
                errors.append(f"Project module is not source: {name}: {path}")
            else:
                loaded.append(dict(module=name, path=path.relative_to(ROOT).as_posix(),
                                   sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        elif name.split(".")[0] in PREFIXES:
            errors.append(f"Project module loaded outside source root: {name}: {path}")
    OUTPUT.write_text(json.dumps(dict(modules=loaded, errors=errors), indent=2) + "\n")
    if errors:
        session.exitstatus = 1
