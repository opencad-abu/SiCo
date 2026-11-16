"""Actual bundled app-server shell observations through a local test gateway."""

import shlex
import time
from concurrent.futures import ThreadPoolExecutor

from test_agent_codex_native import (  # noqa: F401
    CONTEXT, answer, gateway, native_codex, registry, settings,
)
from test_codex_native_evidence import execute

from sico.codex.backend import CodexBackend
from sico.service.workbench_data import WorkbenchService
from sico.storage.journal import SessionJournal


def test_real_native_shell_has_output_and_real_process_observation(tmp_path, gateway, native_codex):
    middle, end = tmp_path / "emit-middle", tmp_path / "emit-end"
    command = ("sleep .3; printf process-panel-start; "
               f"while [ ! -f {shlex.quote(str(middle))} ]; do sleep .05; done; "
               "printf process-panel-middle; "
               f"while [ ! -f {shlex.quote(str(end))} ]; do sleep .05; done; "
               "printf process-panel-end")
    gateway.responses.put(execute(command, "process"))
    gateway.responses.put(answer("Process probe complete."))
    with SessionJournal(tmp_path, "process-native") as journal:
        backend = CodexBackend(settings(gateway, native_codex, "native"), registry([]), journal, CONTEXT)
        service = WorkbenchService()
        source = service.source(journal)
        try:
            with ThreadPoolExecutor(1) as pool:
                running = pool.submit(lambda: list(backend.run("Run the process inspection probe.")))
                deadline, seen = time.monotonic() + 20, []
                while not running.done() and time.monotonic() < deadline:
                    seen.extend(source.processes().result(5)["rows"])
                    time.sleep(.05)
                events = running.result(10)
            assert events[-1]["kind"] == "task.completed", events[-4:]
            assert any(r["pid"] and r["state_label"] == "运行中" for r in seen), seen
            row = next(r for r in source.processes().result(5)["rows"]
                       if r["id"].startswith("command_") and "process-panel-start" in r["command"])
            middle.touch()
            deadline = time.monotonic() + 10
            while True:
                backend.poll_thread_names()
                detail = source.detail("process", row["id"]).result(5)
                if "process-panel-startprocess-panel-middle" in detail["output"]:
                    break
                assert time.monotonic() < deadline, detail
                time.sleep(.05)
            assert detail["process_state"] == "running"
            assert any(e["kind"] == "codex.history.output" for e in journal.events())
            end.touch()
            deadline = time.monotonic() + 10
            while True:
                backend.poll_thread_names()
                detail = source.detail("process", row["id"]).result(5)
                if detail["process_state"] == "completed":
                    break
                assert time.monotonic() < deadline, detail
                time.sleep(.05)
            assert "process-panel-startprocess-panel-middleprocess-panel-end" in detail["output"]
            assert detail["process_state"] == "completed"
            (tmp_path / "process-observations.txt").write_text(repr(seen))
        finally:
            middle.touch()
            end.touch()
            backend.close()
            service.close()
            assert service.wait(5)
