"""Shared subprocess fixtures for nl2view source acceptance cases."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple


CAD_ROOT = Path(__file__).resolve().parents[3]
ENTRY = CAD_ROOT.parent / "bin/nl2view"

def _write_fake_cds_text_to_5x(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

with open(os.environ["NL2VIEW_TEST_RECORD"], "w", encoding="utf-8") as stream:
    json.dump(
        {
            "argv": sys.argv[1:],
            "cwd": os.getcwd(),
            "ld_library_path": os.environ.get("LD_LIBRARY_PATH"),
            "nolink": os.environ.get("CDS5X_NOLINK"),
            "pythonhome": os.environ.get("PYTHONHOME"),
            "pythonpath": os.environ.get("PYTHONPATH"),
            "ld_preload": os.environ.get("LD_PRELOAD"),
            "ld_audit": os.environ.get("LD_AUDIT"),
            "mps_selectors": sorted(
                name for name in os.environ if name.startswith("CDS_MPS_")
            ),
            "temp": {
                name: os.environ.get(name)
                for name in (
                    "CAD_TEMP_DIR", "SICO_TEMP_DIR", "TMPDIR", "TMP", "TEMP",
                    "SQLITE_TMPDIR", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
                )
            },
        },
        stream,
    )
print("fake cdsTextTo5x stdout")
print("fake cdsTextTo5x stderr", file=sys.stderr)
if os.environ.get("NL2VIEW_TEST_DIAGNOSTIC"):
    print(os.environ["NL2VIEW_TEST_DIAGNOSTIC"])
if not os.environ.get("NL2VIEW_TEST_NO_ARTIFACT"):
    arguments = sys.argv[1:]
    option = lambda name: arguments[arguments.index(name) + 1]
    library = option("-LIB")
    cell = option("-CELL")
    view = option("-VIEW")
    language = option("-LANG")
    source = Path(arguments[-1])
    cds_lib = Path(option("-CDSLIB"))
    configured_library_path = os.environ.get("NL2VIEW_TEST_IMPORT_LIBRARY_PATH")
    library_path = Path(configured_library_path) if configured_library_path else None
    if library_path is None:
        for line in cds_lib.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if "#" in fields:
                fields = fields[:fields.index("#")]
            if len(fields) == 3 and fields[0] in {"DEFINE", "SOFTDEFINE"} and fields[1] == library:
                library_path = Path(os.path.expandvars(fields[2]))
                if not library_path.is_absolute():
                    library_path = cds_lib.parent / library_path
    if library_path is None:
        library_path = Path(os.environ["NL2VIEW_TEST_LIBRARY_PATH"])
    view_directory = library_path / cell / view
    view_directory.mkdir(parents=True, exist_ok=True)
    master_name = {
        "spectre": "spectre.spectre",
        "hspice": "hspice.hsp",
        "spice": "spice.spc",
        "dspf": "design.dspf",
    }[language]
    master = view_directory / master_name
    if master.exists() or master.is_symlink():
        master.unlink()
    if os.environ.get("CDS5X_NOLINK"):
        master.write_bytes(source.read_bytes())
    else:
        master.symlink_to(source)
    (view_directory / "master.tag").write_text(
        "-- Master.tag File, Rev:1.0\\n" + master_name + "\\n",
        encoding="utf-8",
    )
    (view_directory / "netlist.oa").write_bytes(
        b"fake OA connectivity " + source.read_bytes()
    )
    if os.environ.get("NL2VIEW_TEST_PRESERVE_METADATA"):
        metadata = json.loads(os.environ["NL2VIEW_TEST_PRESERVE_METADATA"])
        for artifact in view_directory.iterdir():
            preserved = metadata.get(artifact.name)
            if preserved:
                os.utime(artifact, ns=tuple(preserved))
raise SystemExit(int(os.environ.get("NL2VIEW_TEST_EXIT", "0")))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path

def _write_fake_cds_lib_debug(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

with open(os.environ["NL2VIEW_TEST_CDSLIBDEBUG_RECORD"], "w", encoding="utf-8") as stream:
    json.dump(sys.argv[1:], stream)
library_path = Path(os.environ["NL2VIEW_TEST_LIBRARY_PATH"])
print("Parsing cdslib file " + sys.argv[sys.argv.index("-cdslib") + 1] + ".")
print("\\nLibraries defined:\\n")
print("Defined in test cds.lib:")
print("Line #  Filesys  Path")
print("------  ----     ----")
print("   1    work     " + str(library_path))
raise SystemExit(int(os.environ.get("NL2VIEW_TEST_CDSLIBDEBUG_EXIT", "0")))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path

def _write_slow_importer(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env python3
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"]
)
Path(os.environ["NL2VIEW_TEST_PARENT_PID"]).write_text(str(os.getpid()))
Path(os.environ["NL2VIEW_TEST_CHILD_PID"]).write_text(str(child.pid))
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(1)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path

def _write_signal_recording_importer(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env python3
import os
import signal
import time
from pathlib import Path

ready = Path(os.environ["NL2VIEW_TEST_READY"])
received = Path(os.environ["NL2VIEW_TEST_SIGNAL"])

def stop(signum, _frame):
    received.write_text(str(signum))
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
ready.write_text(str(os.getpid()))
while True:
    time.sleep(1)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path

def _wait_for_file(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")

def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = Path(f"/proc/{pid}/stat")
    if status.is_file():
        fields = status.read_text(encoding="ascii", errors="replace").split()
        if len(fields) > 2 and fields[2] == "Z":
            return False
    return True

def _run(
    tmp_path: Path,
    *arguments: str,
    extra_environment: Optional[Dict[str, str]] = None,
) -> Tuple[subprocess.CompletedProcess, Optional[Dict[str, object]]]:
    fake = _write_fake_cds_text_to_5x(tmp_path / "cdsTextTo5x")
    record = tmp_path / "record.json"
    environment = os.environ.copy()
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    for name in (
        "DRC_ORIG_LD_LIBRARY_PATH",
        "LEF_ORIG_LD_LIBRARY_PATH",
        "LVS_ORIG_LD_LIBRARY_PATH",
        "NL2VIEW_ORIG_LD_LIBRARY_PATH",
        "MTS_NETLISTOR_ORIG_LD_LIBRARY_PATH",
        "RCE_ORIG_LD_LIBRARY_PATH",
    ):
        environment.pop(name, None)
    environment.pop("LD_LIBRARY_PATH", None)
    for name in (
        "CAD_TEMP_DIR",
        "SICO_TEMP_DIR",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SQLITE_TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
    ):
        environment.pop(name, None)
        environment.pop(f"CAD_ORIG_{name}_SET", None)
        environment.pop(f"CAD_ORIG_{name}", None)
        environment.pop(f"SICO_ORIG_{name}_SET", None)
        environment.pop(f"SICO_ORIG_{name}", None)
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "NL2VIEW_TEST_RECORD": str(record),
            "NL2VIEW_TEST_LIBRARY_PATH": str(tmp_path / "work"),
        }
    )
    if extra_environment:
        environment.update(extra_environment)
    completed = subprocess.run(
        [str(ENTRY), "--cds-text-to-5x", str(fake), *arguments],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(record.read_text(encoding="utf-8")) if record.exists() else None
    return completed, payload
