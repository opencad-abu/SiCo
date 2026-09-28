from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import threading
import time

import pytest

from mtsnetlistor.model import SourceDesign
from mtsnetlistor.process import run_isolated
from mtsnetlistor.project_worker import ProjectWorker, active_project_worker
from project_worker_fake import fake_ocean


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.fixture
def runtime(tmp_path, protected_worker_context):
    executable = fake_ocean(tmp_path)
    cds = tmp_path / "cds.lib"
    cds.write_text("")
    env = dict(os.environ, PATH=str(tmp_path) + ":" + os.environ.get("PATH", ""))
    source = SourceDesign(cds, "work", "a")
    script = tmp_path / "task.ocn"
    script.write_text('printf("hello")\nexit()\n')
    worker = ProjectWorker()
    yield worker, source, script, env, executable
    worker.close()


def run(runtime, **kw):
    w, source, script, env, executable = runtime
    return w.run(
        script, cwd=script.parent, environment=env, executable=str(executable), **kw
    )


def test_same_project_reuses_process_and_returns_task_output(runtime):
    w, source, script, env, executable = runtime
    with w.scope(source, env):
        first = run(runtime)
    with w.scope(replace(source, cell="b"), env):
        second = run_isolated(
            [str(executable), "-replay", str(script)],
            cwd=script.parent,
            environment=env,
            output_callback=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    assert first.pid == second.pid and w.starts == 1
    assert first.stdout == second.stdout == "task-result\n"
    assert first.task_id != second.task_id
    assert first.lifetime == "project" and first.runtime_pid == first.pid
    assert first.worker_started_at == second.worker_started_at
    assert alive(first.pid)
    assert active_project_worker() is None
    root = w.root
    assert not list(root.glob("task-*.il"))
    w.close()
    assert not alive(first.pid) and not root.exists()


@pytest.mark.parametrize(
    "change", ["project", "include", "environment", "startup", "startup_removed"]
)
def test_project_configuration_change_retires_old_group(runtime, change):
    w, source, script, env, _ = runtime
    include = script.parent / "included.lib"
    include.write_text("")
    source.cds_lib.write_text(f"INCLUDE {include}\n")
    startup = script.parent / "setup.il"
    startup.write_text("t\n")
    source = replace(source, startup_file=startup)
    with w.scope(source, env):
        first = run(runtime)
    if change == "project":
        other = script.parent / "other.lib"
        other.write_text("")
        source = replace(source, cds_lib=other)
    elif change == "include":
        include.write_text("DEFINE different /tmp/different\n")
    elif change == "environment":
        env = dict(env, MTS_TEST_PROJECT="different")
    elif change == "startup":
        startup.write_text("nil\n")
    else:
        source = replace(source, startup_file=None)
    with w.scope(source, env):
        assert not alive(first.pid)
        second = run(runtime)
    assert first.pid != second.pid and w.starts == 2


@pytest.mark.parametrize("failure", ["timeout", "cancel", "crash", "error"])
def test_failed_task_cleans_group_then_retry_succeeds(runtime, failure):
    w, source, script, env, _ = runtime
    with w.scope(source, env):
        first = run(runtime)
        script.write_text(
            {
                "timeout": "FAKE_HANG",
                "cancel": "FAKE_HANG",
                "crash": "FAKE_CRASH",
                "error": "FAKE_ERROR",
            }[failure]
            + "\nexit()\n"
        )
        cancel = threading.Event()
        stop = threading.Event()

        def cancel_when_entered():
            while not stop.wait(0.01):
                if (script.parent / "entered").exists():
                    cancel.set()
                    return

        watcher = threading.Thread(target=cancel_when_entered)
        if failure == "cancel":
            watcher.start()
        try:
            result = run(
                runtime, timeout=0.3 if failure == "timeout" else 10, cancel=cancel
            )
        finally:
            stop.set()
            if watcher.ident:
                watcher.join(2)
        assert result.returncode != 0
        assert result.timed_out == (failure == "timeout")
        assert result.canceled == (failure == "cancel")
        assert w.process is None and not alive(first.pid)
        marker = script.parent / "helper.pid"
        if marker.exists():
            assert not alive(int(marker.read_text()))
        script.write_text("t\nexit()\n")
        retried = run(runtime)
        assert retried.returncode == 0 and retried.pid != first.pid


def test_supervisor_exception_and_validation_failure_retire_runtime(runtime):
    w, source, script, env, _ = runtime
    with pytest.raises(RuntimeError, match="validation"):
        with w.scope(source, env):
            result = run(runtime)
            raise RuntimeError("validation")
    assert not alive(result.pid) and w.root is None

    class BadCancel:
        def is_set(self):
            raise RuntimeError("supervisor")

    with w.scope(source, env):
        result = run(runtime)
        with pytest.raises(RuntimeError, match="supervisor"):
            run(runtime, cancel=BadCancel())
    assert not alive(result.pid)


def test_canceled_queued_request_never_starts(runtime):
    w, source, script, env, _ = runtime
    cancel = threading.Event()
    cancel.set()
    with w.scope(source, env):
        result = run(runtime, cancel=cancel)
    assert result.canceled and w.starts == 0


def test_cached_catalog_warms_runtime_and_stale_reset_cannot_close_new_one(
    runtime, monkeypatch
):
    from cadview.catalog import Catalog
    from mtsnetlistor.catalog import CatalogResult
    from mtsnetlistor.gui import controller as module

    w, source, script, env, _ = runtime
    cached = CatalogResult(Catalog(source.cds_lib, ()), True, "dbAccess")
    monkeypatch.setattr(module, "load_source_catalog", lambda *a, **k: cached)
    controller = module.MtsController(persistent_source=True)
    controller._project_worker = w
    try:
        controller.refresh_source_catalog(source.cds_lib, environment=env)
        deadline = time.monotonic() + 5
        while controller.state.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        assert controller.state.error == ""
        first = w.process.pid
        # Model the adverse lock order: new source operation wins before the
        # queued old cleanup. Its epoch must protect the replacement worker.
        controller._source_epoch += 1
        with controller._worker_lock:
            controller._source_call(module.load_source_catalog, source.cds_lib, env)
        second = w.process.pid
        assert first != second and not alive(first)
        controller._close_project_worker(controller._source_epoch)
        assert alive(second)
    finally:
        controller.close(wait=False)
        controller.close(wait=True)
    assert not alive(second)


def test_publication_failure_keeps_source_runtime_available(runtime, monkeypatch):
    from mtsnetlistor.gui import controller as module
    from mtsnetlistor.model import NetlistRequest

    w, source, script, env, _ = runtime
    monkeypatch.setattr(module, "generate", lambda *a, **kw: run(runtime))

    def failed_publish(*args, **kwargs):
        raise RuntimeError("publication validation failed")

    monkeypatch.setattr(module, "publish_bundle", failed_publish)
    controller = module.MtsController(persistent_source=True, session=object())
    controller._project_worker = w

    def finished():
        deadline = time.monotonic() + 5
        while controller.state.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not controller.state.busy

    try:
        controller.generate(NetlistRequest(source), environ=env)
        finished()
        assert not controller.state.error
        pid = w.process.pid
        controller.publish(
            NetlistRequest(source), netlist=script, run_dir=script.parent
        )
        finished()
        assert "publication validation failed" in controller.state.error
        for _ in range(2):
            controller.generate(NetlistRequest(source), environ=env)
            finished()
            assert not controller.state.error and w.process.pid == pid
    finally:
        controller.close()


def test_idle_crash_and_executable_change_are_recovered(runtime):
    w, source, script, env, executable = runtime
    with w.scope(source, env):
        first = run(runtime)
        w.process.terminate()
        w.process.wait(timeout=2)
        second = run(runtime)
        assert second.returncode == 0 and second.pid != first.pid
        another = executable.with_name("ocean-new")
        another.write_text(executable.read_text())
        another.chmod(0o700)
        third = w.run(
            script, cwd=script.parent, environment=env, executable=str(another)
        )
        assert third.pid != second.pid and not alive(second.pid)


def test_controller_shutdown_reports_cleanup_failure(monkeypatch):
    from mtsnetlistor.gui.controller import MtsController

    controller = MtsController(persistent_source=True)

    def failed_close():
        raise RuntimeError("worker group did not terminate")

    monkeypatch.setattr(controller._project_worker, "close", failed_close)
    with pytest.raises(RuntimeError, match="did not terminate"):
        controller.close(wait=True)
