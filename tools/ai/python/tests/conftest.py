from __future__ import annotations

import gzip
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
for source in (PYTHON_ROOT, PYTHON_ROOT.parents[1] / "sico/python",
               PYTHON_ROOT.parents[1] / "common/python"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))


@pytest.fixture(scope="session", autouse=True)
def source_process_environment():
    """Child test processes use this source installation even after changing CWD."""
    original = os.environ.get("PYTHONPATH")
    installation = {key: os.environ.get(key) for key in ("SICO_HOME", "CAD_HOME")}
    os.environ["SICO_HOME"] = str(PYTHON_ROOT.parents[2])
    os.environ.pop("CAD_HOME", None)
    paths = [PYTHON_ROOT, PYTHON_ROOT.parents[1] / "sico/python",
             PYTHON_ROOT.parents[1] / "common/python"]
    paths.extend(Path(value).resolve() for value in (original or "").split(os.pathsep) if value)
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(map(str, paths)))
    yield
    if original is None:
        os.environ.pop("PYTHONPATH", None)
    else:
        os.environ["PYTHONPATH"] = original
    for key, value in installation.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(scope="session", autouse=True)
def qt_application_lifetime():
    # GUI modules import Qt at collection time. Keep its application alive
    # across tests, including asynchronous window and service cleanup.
    widgets = sys.modules.get("PyQt5.QtWidgets")
    if widgets is None:
        yield
        return
    application = widgets.QApplication.instance() or widgets.QApplication([])
    application.setQuitOnLastWindowClosed(False)
    yield application
    application.processEvents()


@pytest.fixture(autouse=True)
def isolated_project_memory(request, monkeypatch):
    """Keep unrelated tests deterministic; memory tests opt into the default."""
    if request.node.get_closest_marker("project_memory"):
        yield
        return
    from sico.codex import thread_memory

    monkeypatch.setattr(thread_memory, "DEFAULT_MEMORY", {
        "enabled": False, "generate": False, "use": False,
    })
    yield


@pytest.fixture(autouse=True)
def capture_startup_resources(request, monkeypatch):
    if request.path.name in {
        "test_agent_startup_bootstrap.py", "test_agent_startup_activation.py",
        "test_agent_credentials.py", "test_agent_local_startup.py",
        "test_agent_connection_runtime.py",
    }:
        from agent_startup_fixture import capture_startup

        capture_startup(monkeypatch)


@pytest.fixture(autouse=True)
def qt_window_cleanup(qt_application_lifetime):
    yield
    if qt_application_lifetime is None:
        return
    from PyQt5 import sip
    from PyQt5.QtCore import QEvent

    application = qt_application_lifetime
    windows = application.topLevelWidgets()
    for window in windows:
        if sip.isdeleted(window):
            continue
        sessions = getattr(getattr(window, "api", None), "sessions", None)
        if sessions is None:
            sessions = getattr(window, "sessions", None)
        if sessions is not None:
            sessions.close()
            assert sessions.wait(5), "Desktop services outlived their test window"
        # Teardown must not reopen the project-close policy dialog.
        if getattr(window, "lifecycle", None) is not None:
            window.lifecycle.closing = True
        window.close()
        window.deleteLater()
    application.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()


@pytest.fixture
def codex_stub(tmp_path: Path) -> Path:
    executable = tmp_path / "stub-codex"
    executable.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then printf "codex-cli 0.162.1\\n"; fi\n',
        encoding="ascii",
    )
    executable.chmod(0o755)
    return executable


@pytest.fixture
def reference_root(tmp_path: Path) -> Path:
    root = tmp_path / "cadence-skill"
    root.mkdir()
    connection = sqlite3.connect(root / "skill_api.sqlite3")
    connection.executescript(
        """
        PRAGMA user_version = 1;
        CREATE TABLE functions (
            reference_id TEXT PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE,
            usage TEXT NOT NULL,
            description TEXT NOT NULL,
            parameters_json TEXT NOT NULL,
            returns_text TEXT NOT NULL,
            example TEXT NOT NULL,
            doc_set TEXT NOT NULL,
            document TEXT NOT NULL,
            doc_title TEXT NOT NULL,
            product_version TEXT NOT NULL,
            body TEXT NOT NULL
        );
        CREATE INDEX functions_name ON functions(name COLLATE NOCASE);
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    rows = [
        (
            "skill-language/substring",
            "substring",
            "substring( S_string x_index [ x_length ] )",
            "Returns part of a string.",
            "[]",
            "A string.",
            'substring("abc" 1 1)',
            "SKILL Language User Guide",
            "sklangref/substring.html",
            "substring",
            "ICADVM 23.1",
            "Syntax\nsubstring( S_string x_index [ x_length ] )\nDescription\n"
            "Returns a substring beginning at x_index.\nArguments\nx_length is optional.",
        ),
        (
            "virtuoso-db/dbOpenCellViewByType",
            "dbOpenCellViewByType",
            "dbOpenCellViewByType( t_lib t_cell t_view [ t_viewType ] [ t_mode ] )",
            "Opens a cellview.",
            "[]",
            "A database object or nil.",
            "",
            "Virtuoso Design Environment SKILL Reference",
            "vdbref/dbOpenCellViewByType.html",
            "Opening a cellview",
            "ICADVM 23.1",
            "Syntax\ndbOpenCellViewByType(...)\nDescription\nOpens an existing database cellview.",
        ),
        (
            "test/longDocument",
            "longDocument",
            "longDocument()",
            "A deliberately long reference entry.",
            "[]",
            "t",
            "",
            "Test Reference",
            "test/longDocument.html",
            "Long document",
            "test",
            "Description\n" + "bounded documentation " * 100,
        ),
    ]
    connection.executemany(
        "INSERT INTO functions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    connection.executemany(
        "INSERT INTO metadata VALUES (?, ?)",
        [
            ("function_count", str(len(rows))),
            ("document_count", "3"),
            ("copyrights", json.dumps(["Copyright 2025 Cadence Design Systems, Inc."])),
        ],
    )
    connection.commit()
    connection.close()
    fallback = {
        "schema_version": 1,
        "metadata": {
            "function_count": len(rows),
            "document_count": 3,
            "copyrights": ["Copyright 2025 Cadence Design Systems, Inc."],
        },
        "functions": [
            {
                "reference_id": row[0],
                "name": row[1],
                "usage": row[2],
                "doc_set": row[7],
                "document": row[8],
                "doc_title": row[9],
                "product_version": row[10],
                "body": row[11],
            }
            for row in rows
        ],
    }
    encoded = json.dumps(
        fallback, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    (root / "skill_api.json.gz").write_bytes(gzip.compress(encoded, compresslevel=9, mtime=0))
    return root
