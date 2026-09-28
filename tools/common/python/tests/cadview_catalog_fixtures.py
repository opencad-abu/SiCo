from __future__ import annotations

import json
import os
import ast
from pathlib import Path
import shutil
from threading import Event, Thread
import time

import pytest

from cadview.catalog import (
    Catalog,
    CatalogCategory,
    CatalogCancelled,
    CatalogCell,
    CatalogCombineGroup,
    CatalogError,
    CatalogLibrary,
    CatalogTimeout,
    CatalogView,
    dbaccess_catalog,
    filesystem_catalog,
)
from cadview.cdslib import LibraryDefinitions, read_library_definitions


def _library_tree(root: Path) -> tuple[Path, Path]:
    work = root / "work"
    source = root / "source"
    (work / "zcell" / "layout").mkdir(parents=True)
    (work / "zcell" / "schematic").mkdir()
    (work / "acell" / "schematic").mkdir(parents=True)
    (source / "only_cell" / "schematic").mkdir(parents=True)
    cds = root / "cds.lib"
    cds.write_text(
        "DEFINE work ./work\n"
        "DEFINE source ./source\n"
        "UNDEFINE missing\n",
        encoding="utf-8",
    )
    return cds, work



def _write_provider(path: Path, *, delay: float = 0.0) -> Path:
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
import time
if float(os.environ.get("CATALOG_TEST_DELAY", "0")) > 0:
    time.sleep(float(os.environ["CATALOG_TEST_DELAY"]))
print(json.dumps({
    "schema_version": 1,
    "authoritative": True,
    "libraries": [{
        "name": "zlib", "path": "/tmp/zlib", "writable": True,
        "cells": [{"name": "zcell", "views": ["schematic", "layout"]}]
    }, {
        "name": "alib", "path": "/tmp/alib", "writable": False,
        "cells": []
    }]
}))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path

def _write_json_provider(path: Path, payload: dict) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        f"print(json.dumps({payload!r}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path

def _write_private_protocol_provider(
    path: Path,
    marker: Path,
    *,
    exit_status: int = 0,
    delay: float = 0.0,
) -> Path:
    """Create a provider that exercises the default private-file protocol."""

    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import time

protocol = os.environ["CADVIEW_CATALOG_OUTPUT"]
open(%r, "w", encoding="utf-8").write(protocol)
if %r:
    time.sleep(%r)
print("license/PDK diagnostic noise")
payload = {
    "schema_version": 1,
    "authoritative": True,
    "libraries": [{
        "name": "zlib", "path": "/tmp/zlib", "writable": True,
        "cells": [{"name": "zcell", "views": ["schematic"]}],
    }],
}
with open(protocol, "w", encoding="utf-8") as stream:
    json.dump(payload, stream)
raise SystemExit(%r)
"""
        % (str(marker), bool(delay), delay, exit_status),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path

__all__ = [name for name in globals() if not name.startswith("__")]
