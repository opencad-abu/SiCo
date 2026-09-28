"""Selector eligibility consumes values without accessing Qt or owners."""

from dataclasses import FrozenInstanceError, replace

import pytest

from cadlsf_gui_fixtures import _snapshot
from cadlsf.gui.selection_state import selection_state
from cadlsf.model import HostInfo


@pytest.mark.parametrize(
    "change, expected",
    [
        ({}, (True, True)),
        ({"busy": True}, (False, False)),
        ({"closing": True}, (False, False)),
        ({"status": "stale"}, (False, False)),
        ({"queue": "batch"}, (False, False)),
        ({"snapshot": None}, (False, False)),
        ({"host": None}, (True, False)),
        ({"host": HostInfo("missing", "ok", True)}, (True, False)),
        ({"host": HostInfo("node-idle", "closed", False)}, (True, False)),
    ],
)
def test_selection_eligibility(change, expected):
    snapshot = _snapshot()
    values = dict(
        snapshot=snapshot,
        queue="normal",
        host=snapshot.hosts[1],
        busy=False,
        status="ready",
        closing=False,
    )
    state = selection_state(**{**values, **change})
    assert (state.queue_eligible, state.host_eligible) == expected
    assert state.fingerprint(False) == (state.queue, None)
    assert state.fingerprint(True) == (state.queue, state.host)
    with pytest.raises(FrozenInstanceError):
        state.queue = "batch"


@pytest.mark.parametrize(
    "failure", ["closed_queue", "failed_snapshot", "unavailable_host"]
)
def test_selection_checks_authoritative_snapshot(failure):
    snapshot = _snapshot()
    selected = snapshot.hosts[1]
    if failure == "closed_queue":
        snapshot = replace(
            snapshot, queues=(replace(snapshot.queues[0], is_open=False),)
        )
    elif failure == "failed_snapshot":
        snapshot = replace(snapshot, status="error")
    else:
        snapshot = replace(snapshot, hosts=(replace(selected, is_available=False),))
    state = selection_state(
        snapshot,
        queue="normal",
        host=selected,
        busy=False,
        status="ready",
        closing=False,
    )
    assert state.queue_eligible == (failure == "unavailable_host")
    assert not state.host_eligible
