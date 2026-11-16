"""Owned native test session and fixture-only master sampling for creator acceptance.

Session and bridge plumbing lives in :mod:`cadai.headless_session`; this module
keeps the pytest-friendly wrapper and the fixture-only master sampler, and
re-exports the production names the test fixtures rely on.
"""

import json
import os
import shutil
from contextlib import contextmanager

import pytest

from cadai.headless_session import (  # noqa: F401  (re-export for test fixtures)
    AI_ROOT,
    IC_LIBS,
    MODULES,
    DispatchClient,
    NativeBridge,
)
from cadai.headless_session import session as _session


@contextmanager
def session(path, *, worklib="genericWork"):
    if (not shutil.which(os.environ.get("SICO_AI_VIRTUOSO", "virtuoso"))
            or not shutil.which("Xvfb")):
        pytest.skip("Virtuoso/Xvfb unavailable")
    with _session(path, worklib=worklib) as trio:
        yield trio


def sample_master(bridge, cell, library="analogLib"):
    # Test fixture only: exact selected basic cells, not a production discovery tool.
    code = """let((cv cdf rows points box shapes)
      cv=dbOpenCellViewByType(LIB CELL "symbol" "" "r")
      cdf=cdfGetCellCDF(ddGetObj(LIB CELL))
      rows=aiCircuitJson(list(cv~>bBox cv~>isParamCell cv~>superMaster
        cdf~>formInitProc cdf~>doneProc
        foreach(mapcar p cdf~>parameters
          list(p~>name p~>paramType p~>editable p~>callback p~>defValue))
        foreach(mapcar term cv~>terminals list(term~>name term~>direction
          foreach(mapcar pin term~>pins list(pin~>fig~>objType pin~>fig~>bBox pin~>fig~>points))))
        foreach(mapcar sh cv~>shapes list(sh~>objType sh~>lpp sh~>bBox))))
      dbClose(cv) rows)""".replace("CELL", json.dumps(cell)).replace("LIB", json.dumps(library))
    return json.loads(bridge.skill(code))
