"""Opt-in licensed test; all OA writes are confined to pytest's private library."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import threading
import time

import pytest

from mtsnetlistor.catalog import load_source_catalog
from mtsnetlistor.defaults import DefaultsProbeRequest
from mtsnetlistor.model import ModelEntry, NetlistRequest, ProcessOptions, SourceDesign
from mtsnetlistor.project_protocol import skill_string
from mtsnetlistor.project_worker import ProjectWorker
from mtsnetlistor.workflow import generate, probe_defaults

pytestmark = pytest.mark.skipif(
    os.environ.get("MTS_PERSISTENT_NATIVE") != "1",
    reason="set MTS_PERSISTENT_NATIVE=1 with a licensed OCEAN environment",
)


def test_native_project_reuse_isolation_recovery(tmp_path):
    base = Path(os.environ["CDSHOME"]) / "tools/dfII/etc/cdslib"
    cds = tmp_path / "cds.lib"
    library = tmp_path / "library"
    cds.write_text(
        f"DEFINE analogLib {base}/artist/analogLib\n"
        f"DEFINE basic {base}/basic\nDEFINE mtsNative {library}\n"
    )
    env = dict(os.environ, PROJ_ADE_DB_DIR=str(tmp_path / "ade"))
    source = SourceDesign(cds, "mtsNative", "cellA")
    script = tmp_path / "task.ocn"
    fixture = Path(__file__).parent / "fixtures/project_resistor.il"
    script.write_text(
        f'ddCreateLib("mtsNative" {skill_string(library)})\nddUpdateLibList()\n'
        f"load({skill_string(fixture.resolve())})\n"
        'mtsFixtureResistor("mtsNative" "cellA" "1000")\n'
        'mtsFixtureResistor("mtsNative" "cellB" "2000")\nexit()\n'
    )
    evidence = []
    w = ProjectWorker()

    def measured(name, action):
        start = time.monotonic()
        result = action()
        process = getattr(result, "process", result)
        evidence.append(
            dict(
                task=name,
                seconds=round(time.monotonic() - start, 3),
                pid=getattr(process, "pid", None),
                runtime_pid=getattr(process, "runtime_pid", None),
            )
        )
        return result

    def task(text, **kwargs):
        script.write_text(text + "\nexit()\n")
        return w.run(
            script,
            cwd=tmp_path,
            environment=env,
            timeout=kwargs.pop("timeout", 60),
            **kwargs,
        )

    try:
        with w.scope(source, env):
            first = measured(
                "cold_fixture",
                lambda: w.run(script, cwd=tmp_path, environment=env, timeout=90),
            )
            assert first.returncode == 0, first.stdout
            pid = first.pid
            defaults = {}
            for dialect in ("spectre", "hspiceD"):
                defaults[dialect] = measured(
                    "defaults_" + dialect,
                    lambda: probe_defaults(
                        DefaultsProbeRequest(source, dialect), environ=env
                    ),
                )
            model = tmp_path / "model.scs"
            model.write_text("// unique test model\n")
            a = measured(
                "spectre_override",
                lambda: generate(
                    NetlistRequest(
                        source,
                        models=(ModelEntry(model),),
                        process_options=ProcessOptions(temp=81, gmin="2e-9"),
                    ),
                    environ=env,
                ),
            )
            assert str(model) in a.raw_netlist.read_text()
            b = measured(
                "spectre_other_cell",
                lambda: generate(
                    NetlistRequest(replace(source, cell="cellB")), environ=env
                ),
            )
            text = b.raw_netlist.read_text()
            assert (
                str(model) not in text
                and "temp=81" not in text
                and "gmin=2e-09" not in text
            )
            assert "cellB" in b.scoped_netlist.read_text()
            measured(
                "hspice",
                lambda: generate(NetlistRequest(source, "hspiceD"), environ=env),
            )
            a2 = measured(
                "spectre_again", lambda: generate(NetlistRequest(source), environ=env)
            )
            assert a.raw_netlist != a2.raw_netlist
            assert str(model) not in a2.raw_netlist.read_text()
            for dialect in ("spectre", "hspiceD"):
                after = measured(
                    "defaults_again_" + dialect,
                    lambda: probe_defaults(
                        DefaultsProbeRequest(source, dialect), environ=env
                    ),
                )
                assert after.baseline == defaults[dialect].baseline
                assert after.after_design == defaults[dialect].after_design
                assert (
                    after.after_startup_simrc == defaults[dialect].after_startup_simrc
                )
            assert {r["pid"] for r in evidence} == {pid} and w.starts == 1
            idle = task('printf("SESSION_AFTER=%L\\n" asiGetCurrentSession())')
            assert "SESSION_AFTER=nil" in idle.stdout
            # Force refresh must see a new cell created by a separate process.
            external = tmp_path / "external.ocn"
            external.write_text(
                f'load({skill_string(fixture.resolve())})\nmtsFixtureResistor("mtsNative" "cellC" "3000")\nexit()\n'
            )
            from mtsnetlistor.environment import isolated_environment
            from mtsnetlistor.process import run_isolated
            from mtsnetlistor.project_worker import _ACTIVE

            token = _ACTIVE.set(None)
            try:
                external_result = run_isolated(
                    [
                        "ocean",
                        "-nograph",
                        "-nocdsinit",
                        "-cdslib",
                        str(cds),
                        "-replay",
                        str(external),
                    ],
                    cwd=tmp_path,
                    environment=isolated_environment(
                        env, cds_lib=cds, workdir=tmp_path
                    ),
                    timeout=60,
                )
                assert external_result.returncode == 0
            finally:
                _ACTIVE.reset(token)
            catalog = load_source_catalog(
                cds, environment=env, force_refresh=True, timeout=60
            )
            assert "cellC" in [
                cell.name
                for lib in catalog.catalog.libraries
                if lib.name == "mtsNative"
                for cell in lib.cells
            ]
            assert w.process.pid == pid
            failed = task('error("intentional task failure")')
            assert failed.returncode != 0 and w.process is None
            retry = measured(
                "retry_after_error", lambda: task('printf("RECOVERED\\n")')
            )
            assert retry.returncode == 0 and retry.pid != pid
            assert "RECOVERED" in retry.stdout
            timed = task("ipcSleepMilli(10000)", timeout=0.3)
            assert timed.timed_out and w.process is None
            recovered = task('printf("AFTER_TIMEOUT\\n")')
            assert recovered.returncode == 0
            cancel = threading.Event()
            timer = threading.Timer(0.2, cancel.set)
            timer.start()
            try:
                canceled = task("ipcSleepMilli(10000)", cancel=cancel)
                assert canceled.canceled and w.process is None
            finally:
                timer.join(1)
            recovered = task('printf("AFTER_CANCEL\\n")')
            assert recovered.returncode == 0
            cleanup = task("simulator('spectre)\nprocedure(ocnCloseSession() nil)")
            assert cleanup.returncode != 0 and w.process is None
            assert task('printf("AFTER_CLEANUP_FAILURE\\n")').returncode == 0
        previous = w.process.pid
        with w.scope(source, dict(env, MTS_TEST_PROJECT="different")):
            assert w.process is None
            new = measured("project_change", lambda: task('printf("NEW_PROJECT\\n")'))
            assert new.pid != previous and new.returncode == 0
        runtime_root = w.root
    finally:
        w.close()
        (tmp_path / "acceptance.json").write_text(json.dumps(evidence, indent=2))
    assert not runtime_root.exists()
