from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from cadgui import __version__
from cadgui.environment import prepare_qt_environment
from cadgui.lifecycle import capture_parent_identity, finalize_gui_exit
from cadgui.protocol import TransferDocument, read_transfer, write_transfer


CAD_ROOT = Path(__file__).resolve().parents[3]


def _write_proc_stat(
    proc_root: Path,
    process_id: int,
    start_time: str,
    *,
    parent_pid: int = 0,
) -> None:
    stat_path = proc_root / str(process_id) / "stat"
    stat_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["S", str(parent_pid), *("0" for _ in range(17)), start_time]
    stat_path.write_text(
        f"{process_id} (virtuoso (main)) {' '.join(fields)}\n",
        encoding="ascii",
    )


def test_package_import_is_qt_lazy() -> None:
    common_python = CAD_ROOT / "common" / "python"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(common_python)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import cadgui, sys; "
            "print(cadgui.__version__); "
            "print(any(name.startswith('PyQt5') for name in sys.modules))",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.stdout.splitlines() == [__version__, "False"]


def test_catalog_package_exports_are_qt_free() -> None:
    common_python = CAD_ROOT / "common" / "python"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(common_python)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import cadview, sys; "
            "print(cadview.Catalog.__name__); "
            "print(cadview.CatalogView.__name__); "
            "print(any(name.startswith('PyQt5') for name in sys.modules))",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.stdout.splitlines() == ["Catalog", "CatalogView", "False"]


def test_qt_environment_cleanup_preserves_platform_and_runtime() -> None:
    environment = {
        "QT_QPA_PLATFORM_PLUGIN_PATH": "/virtuoso/qt/plugins",
        "QT_PLUGIN_PATH": "/virtuoso/qt",
        "QT_DIR": "/virtuoso/qt",
        "QT_SELECT": "qt5",
        "QML2_IMPORT_PATH": "/virtuoso/qml",
        "QT_XCB_NO_XI2": "1",
        "QT_XCB_NO_XI2_MOUSE": "1",
        "QT_QPA_PLATFORM": "offscreen",
        "LD_LIBRARY_PATH": "/python/lib",
    }

    removed = prepare_qt_environment(environment)

    assert set(removed) == {
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QT_PLUGIN_PATH",
        "QT_DIR",
        "QT_SELECT",
        "QML2_IMPORT_PATH",
        "QT_XCB_NO_XI2",
        "QT_XCB_NO_XI2_MOUSE",
    }
    assert environment == {
        "QT_QPA_PLATFORM": "offscreen",
        "LD_LIBRARY_PATH": "/python/lib",
    }


def test_transfer_protocol_round_trip_and_atomic_publication(tmp_path: Path) -> None:
    output = tmp_path / "result.tsv"
    write_transfer(
        output,
        (("GROUP", "METAL"), ("CHECK", "M1.WIDTH")),
        status="applied",
        version="1",
        refuse_existing=True,
    )

    assert output.read_text(encoding="utf-8") == (
        "#version\t1\n#status\tapplied\nGROUP\tMETAL\nCHECK\tM1.WIDTH\n"
    )
    assert read_transfer(output) == TransferDocument(
        (("GROUP", "METAL"), ("CHECK", "M1.WIDTH")),
        status="applied",
        version="1",
    )
    assert not list(tmp_path.glob(".result.tsv.*.tmp"))


def test_transfer_protocol_keeps_unknown_metadata_forward_compatible(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.tsv"
    source.write_text(
        "# comment\n#version\t1\n#feature\tfuture\nGROUP\tMETAL\n",
        encoding="utf-8",
    )

    assert read_transfer(source) == TransferDocument((("GROUP", "METAL"),), version="1")


@pytest.mark.parametrize(
    "content",
    (
        "GROUP\tMETAL\t\n",
        "GROUP\t\tMETAL\n",
        "\tGROUP\tMETAL\n",
        "GROUP\t\n",
        "\tMETAL\n",
        "\t\n",
        "GROUP\tMETAL\tEXTRA\n",
    ),
)
def test_transfer_protocol_rejects_empty_or_extra_tsv_fields(
    tmp_path: Path,
    content: str,
) -> None:
    source = tmp_path / "malformed.tsv"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="Malformed transfer input|non-empty"):
        read_transfer(source)


def test_transfer_protocol_rejects_unsafe_or_ambiguous_documents(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing" / "result.tsv"
    with pytest.raises(FileNotFoundError, match="output directory"):
        write_transfer(missing, (("GROUP", "METAL"),))
    assert not missing.parent.exists()

    existing = tmp_path / "existing.tsv"
    existing.write_text("stale\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        write_transfer(
            existing,
            (("GROUP", "METAL"),),
            refuse_existing=True,
        )
    assert existing.read_text(encoding="utf-8") == "stale\n"

    with pytest.raises(ValueError, match="single-line"):
        write_transfer(tmp_path / "invalid.tsv", (("GROUP", "M1\tWIDTH"),))

    with pytest.raises(TypeError, match="must be a string"):
        write_transfer(tmp_path / "non-string.tsv", (("GROUP", None),))

    duplicate = tmp_path / "duplicate.tsv"
    duplicate.write_text(
        "#status\tapplied\n#status\tapplied\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate transfer status"):
        read_transfer(duplicate)


def test_refuse_existing_is_atomic_between_competing_writers(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.tsv"
    barrier = threading.Barrier(3)
    published: list[str] = []
    rejected: list[str] = []

    def publish(name: str) -> None:
        barrier.wait()
        try:
            write_transfer(output, (("CHECK", name),), refuse_existing=True)
            published.append(name)
        except FileExistsError:
            rejected.append(name)

    threads = [
        threading.Thread(target=publish, args=(name,))
        for name in ("M1.WIDTH", "M1.SPACE")
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert len(published) == 1
    assert len(rejected) == 1
    assert read_transfer(output).records == (("CHECK", published[0]),)
    assert not list(tmp_path.glob(".result.tsv.*.tmp"))


def test_parent_identity_is_parameterized_for_future_gui_flows(
    tmp_path: Path,
) -> None:
    wrapper_pid, owner_pid = 23456, 12345
    _write_proc_stat(tmp_path, wrapper_pid, "111111", parent_pid=owner_pid)
    _write_proc_stat(tmp_path, owner_pid, "987654", parent_pid=1)

    assert capture_parent_identity(
        environ={"FUTURE_GUI_PARENT_PID": str(owner_pid)},
        parent_env="FUTURE_GUI_PARENT_PID",
        proc_root=tmp_path,
        current_pid=34567,
        current_parent_pid=wrapper_pid,
    ) == (owner_pid, "987654")

    with pytest.raises(RuntimeError, match="ancestor"):
        capture_parent_identity(
            owner_pid,
            parent_env="FUTURE_GUI_PARENT_PID",
            proc_root=tmp_path,
            current_pid=34567,
            current_parent_pid=99999,
        )


def test_only_owned_gui_uses_hard_exit_fallback() -> None:
    exits: list[int] = []

    assert finalize_gui_exit(3, None, exits.append) == 3
    assert exits == []
    assert finalize_gui_exit(7, (12345, "987654"), exits.append) == 7
    assert exits == [7]


@pytest.mark.parametrize("entry", ("drc/python/drc", "rce/python/rce"))
def test_production_entry_bootstraps_common_without_pythonpath(entry: str) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, str(CAD_ROOT / entry), "--help"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout
