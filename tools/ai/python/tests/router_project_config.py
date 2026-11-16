# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""Native router acceptance paths and timing contract."""

from pathlib import Path

CAD = Path(__file__).absolute().parents[3]
VIRTUOSO = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso")
CAD_PYTHON = Path("/software/pkgs/python/3.9.13/bin/python3")
SKILL_TIMEOUT = 5.0
FIFO_SLEEP = 2
LATE_SLEEP = 8
LOCK_NAMES = (".lck", ".lock")
