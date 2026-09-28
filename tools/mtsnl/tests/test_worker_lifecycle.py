from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from cadview.catalog import CatalogCancelled, CatalogTimeout, _run_catalog_process
from cadview.process import run_cadence_import
from mtsnetlistor.process import run_isolated
from mtsnetlistor.model import TargetSelection


def _alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def _worker(tmp_path: Path, mode: str) -> tuple[list[str], Path]:
    marker = tmp_path / "descendant.pid"
    child = (
        "import os, signal, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    script = (
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        f"while not Path({str(marker)!r}).exists(): time.sleep(0.01)\n"
        "print('worker ready', flush=True)\n"
        + ("sys.exit(7)\n" if mode == "failure" else "sys.exit(0)\n" if mode == "success" else "time.sleep(60)\n")
    )
    return [sys.executable, "-c", script], marker


@pytest.mark.parametrize("runner", ("mts", "catalog", "import"))
@pytest.mark.parametrize("mode", ("failure", "success", "timeout", "cancel"))
def test_worker_return_waits_for_descendant_cleanup(
    tmp_path: Path, runner: str, mode: str
) -> None:
    command, marker = _worker(tmp_path, mode)
    cancel = threading.Event()
    stop = threading.Event()

    def cancel_ready() -> None:
        while not stop.wait(0.01):
            if marker.exists():
                cancel.set()
                return

    watcher = threading.Thread(target=cancel_ready)
    if mode == "cancel":
        watcher.start()
    try:
        timeout = 0.5 if mode == "timeout" else 10
        if runner == "mts":
            result = run_isolated(command, cwd=tmp_path, environment=os.environ, timeout=timeout, cancel=cancel)
            assert result.timed_out == (mode == "timeout")
            assert result.canceled == (mode == "cancel")
        elif runner == "catalog":
            if mode in {"timeout", "cancel"}:
                with pytest.raises(CatalogTimeout if mode == "timeout" else CatalogCancelled):
                    _run_catalog_process(command, os.environ, timeout=timeout, cancel_event=cancel)
            else:
                result = _run_catalog_process(command, os.environ, timeout=timeout, cancel_event=cancel)
        else:
            result = run_cadence_import(command, os.environ, tmp_path, timeout=timeout, cancel=cancel)
        assert marker.is_file()
        assert not _alive(int(marker.read_text())), "worker returned while its descendant was still running"
        if mode in {"failure", "success"}:
            assert result.returncode == (7 if mode == "failure" else 0)
            assert "worker ready" in result.stdout
    finally:
        stop.set()
        if watcher.ident is not None:
            watcher.join(timeout=1)
        if marker.exists():
            pid = int(marker.read_text())
            if _alive(pid):
                os.kill(pid, signal.SIGKILL)


def test_unexpected_supervisor_exception_cleans_worker(tmp_path: Path) -> None:
    marker = tmp_path / "worker.pid"

    class BrokenCancel:
        def is_set(self) -> bool:
            if marker.exists():
                raise RuntimeError("injected supervisor failure")
            return False

    command = [sys.executable, "-c", f"import os,time; open({str(marker)!r}, 'w').write(str(os.getpid())); time.sleep(60)"]
    try:
        with pytest.raises(RuntimeError, match="supervisor failure"):
            run_isolated(command, cwd=tmp_path, environment=os.environ, cancel=BrokenCancel())
        assert not _alive(int(marker.read_text()))
    finally:
        if marker.exists() and _alive(int(marker.read_text())):
            os.kill(int(marker.read_text()), signal.SIGKILL)


def test_controller_retry_waits_for_canceled_worker_teardown(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtsnetlistor.gui import controller as module

    started = threading.Event()
    cleaning = threading.Event()
    release = threading.Event()
    retried = threading.Event()

    def generate(request: str, **kwargs: object) -> object:
        if request == "first":
            started.set()
            assert kwargs["cancel_event"].wait(2)
            cleaning.set()
            assert release.wait(2)
            raise RuntimeError("first run canceled")
        retried.set()
        return object()

    monkeypatch.setattr(module, "generate", generate)
    controller = module.MtsController()
    try:
        controller.generate("first")
        assert started.wait(1)
        controller.cancel()
        assert cleaning.wait(1)
        controller.generate("retry")
        assert not retried.wait(0.15), "retry overlapped the old worker's teardown"
        release.set()
        assert retried.wait(1)
    finally:
        release.set()
        controller.close()


def test_cleanup_preserves_other_process_groups_and_releases_readers(tmp_path: Path) -> None:
    sibling = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    before_readers = {thread.ident for thread in threading.enumerate()}
    before_fds = len(list(Path("/proc/self/fd").iterdir()))
    try:
        for _ in range(5):
            result = run_isolated([sys.executable, "-c", "print('done')"], cwd=tmp_path, environment=os.environ)
            assert result.returncode == 0
            assert sibling.poll() is None
        assert not [thread for thread in threading.enumerate() if thread.ident not in before_readers and thread.name.startswith("mts-netlistor-")]
        assert len(list(Path("/proc/self/fd").iterdir())) == before_fds
    finally:
        sibling.terminate()
        sibling.wait(timeout=2)


@pytest.mark.usefixtures("protected_worker_context")
def test_publish_validation_failure_then_generate_uses_clean_worker(tmp_path: Path) -> None:
    from dataclasses import replace
    from mtsnetlistor.gui.controller import MtsController
    from test_symbol import _fake_dbaccess, _setup

    _, _, _, session, request = _setup(tmp_path)
    marker = tmp_path / "descendant.pid"
    command, _ = _worker(tmp_path, "failure")
    # Spawn a TERM-resistant helper from the symbol-copy worker, then return
    # a malformed report. This is the publication's post-process failure path.
    helper_code = command[2].split("print('worker ready'", 1)[0]
    dbaccess = _fake_dbaccess(tmp_path / "dbAccess", terminals='("DATA<7:0>"')
    script = dbaccess.read_text().replace("args = sys.argv[1:]", helper_code + "\nargs = sys.argv[1:]")
    dbaccess.write_text(script)
    source_env = dict(os.environ)
    source_env["PROJ_ADE_DB_DIR"] = str(tmp_path / "ade")
    controller = MtsController(session=session)

    def wait_for(predicate: object) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if predicate(controller.state):
                return
            time.sleep(0.01)
        raise AssertionError(str(controller.state))

    try:
        controller.publish(request, netlist=tmp_path / "unused.spe", run_dir=tmp_path / "publish-run", dbaccess=str(dbaccess))
        wait_for(lambda state: not state.busy)
        assert controller.state.publication.status == "manual_cleanup_required"
        assert "invalid terminal list" in controller.state.publication.message
        assert not _alive(int(marker.read_text()))

        ocean = tmp_path / "ocean"
        ocean.write_text(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "raw = Path.cwd() / 'netlist' / 'input.scs'\n"
            "raw.parent.mkdir(exist_ok=True)\n"
            "raw.write_text('simulator lang=spectre\\nsubckt inv A Y\\nends inv\\n')\n"
            "print('MTS_NETLISTOR_RAW=' + str(raw))\n"
        )
        ocean.chmod(0o755)
        for _ in range(2):
            controller.generate(replace(request, target=TargetSelection()), environ=source_env, ocean=str(ocean))
            wait_for(lambda state: not state.busy)
            assert controller.state.stage == "generation_ready", controller.state.error
            assert controller.state.generation.status == "succeeded"
    finally:
        controller.close()
        if marker.exists() and _alive(int(marker.read_text())):
            os.kill(int(marker.read_text()), signal.SIGKILL)
