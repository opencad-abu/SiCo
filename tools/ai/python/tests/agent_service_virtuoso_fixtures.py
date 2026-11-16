"""Opt-in read-only graphical hosts using an existing context, never compilation."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from sico.core.contracts import BoundContext
from sico.transport.bridge_client import BridgeClient

ROOT = Path(__file__).resolve().parents[3]


def fingerprint(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()
            and not p.name.endswith((".lck", ".lock")) and not p.name.startswith(".lock")}


def read_site(path):
    site = json.loads(Path(path).read_text())
    execution = json.loads(Path(site["execution_config"]).read_text())
    runtime = execution["runtime"]
    context = Path(runtime["context"])
    assert hashlib.sha256(context.read_bytes()).hexdigest() == runtime["context_sha256"]
    target = site["target"]
    directory = Path(runtime["libraries"][target["lib"]]) / target["cell"] / target["view"]
    assert directory.is_dir()
    return site, runtime, directory


def startup_source(runtime, target, evidence):
    def quote(value):
        return json.dumps(str(value))
    context = Path(runtime["context"])
    return '\n'.join([
        f'unless(loadContext({quote(context)} t) error("Context load failed"))',
        f'unless(callInitProc({quote(context.stem)}) error("Context init failed"))',
        # The existing context normally receives these roots from the installed CAD launcher.
        # This source-checkout acceptance supplies its root explicitly, without compilation.
        f'aiRoot={quote(ROOT / "ai")}',
        f'sicoRoot={quote(ROOT / "ai")}',
        f'as07Window=deOpenCellView({quote(target["lib"])} {quote(target["cell"])} '
        f'{quote(target["view"])} "schematic" nil "r")',
        'unless(as07Window error("Read-only window unavailable"))',
        'as07Binding=makeTable("as07Binding" nil)',
        "as07Binding['window]=as07Window",
        "as07Binding['entry]=\"as07-readonly\"",
        'as07Bridge=sicoEnsureBridge(as07Binding)',
        'unless(as07Bridge error("Bridge unavailable"))',
        f'as07Out=outfile({quote(evidence / "identity.txt")} "w")',
        'fprintf(as07Out "%s\\n%s\\n%s\\n" '
        "sicoBindings[as07Bridge]['instanceId] "
        "sicoBindings[as07Bridge]['generation] getVersion(t))",
        'close(as07Out)',
        'hiRegTimer("exit(0)" 2900)',
    ]) + '\n'


@contextmanager
def virtuoso_host(project, evidence, site, runtime, display):
    evidence.mkdir(mode=0o700)
    script = evidence / "startup.il"
    script.write_text(startup_source(runtime, site["target"], evidence))
    library = project / "cds.lib"
    library.write_text(''.join(f'DEFINE {name} {path}\n'
                               for name, path in runtime["libraries"].items()))
    environment = dict(site["environment"])
    environment.pop("LD_LIBRARY_PATH", None)
    environment.update(DISPLAY=display, PWD=str(project), CAD_PYTHON=sys.executable,
        SICO_ROOT=str(ROOT / "ai"), CAD_AGENT_ROOT=str(ROOT / "ai"),
        CAD_AI_CONTEXT=runtime["context"], CAD_AI_PYTHON_LIBRARY_PATH="",
        TMPDIR=str(evidence), CDS_AUTO_64BIT="ALL")
    with (evidence / "stdout.log").open("w") as output:
        process = subprocess.Popen([runtime["virtuoso"], "-nocdsinit", "-cdslib", str(library),
            "-restore", str(script), "-log", str(evidence / "CDS.log")], cwd=project,
            env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True)
    try:
        deadline = time.monotonic() + 60
        identity = evidence / "identity.txt"
        while not identity.exists():
            assert process.poll() is None and time.monotonic() < deadline, (
                evidence / "CDS.log").read_text()[-4000:]
            time.sleep(.1)
        instance, generation, version = identity.read_text().splitlines()
        bound = BoundContext(instance, generation, "bound", {})
        with BridgeClient.discover(project, bound) as bridge:
            context = bridge.context(instance, "bound")
            assert context.snapshot["valid"] and context.snapshot["cellview"] == site["target"]
            yield SimpleNamespace(process=process, descriptor=bridge.descriptor,
                                  context=context, version=version)
    finally:
        # Only this test-created host process group, including its bridge, is owned here.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
