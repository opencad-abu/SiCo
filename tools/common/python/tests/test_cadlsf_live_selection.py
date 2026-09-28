from __future__ import annotations


import pytest

from cadlsf.collector import CollectorError, LsfCollector


from cadlsf_fixtures import (
    QUEUE_HEADER,
    HOST_HEADER,
    FakeRunner,
    result as _result,
    command_result as _command_result,
)


def test_live_selector_validation_checks_queue_host_status_and_membership() -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    host_command = ("bhosts", "-w")
    membership_command = (
        "bqueues", "-u", "demo", "-m", "node01", "-w",
    )
    runner = FakeRunner(
        {
            queue_command: _result(
                queue_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            ),
            host_command: _result(
                host_command,
                HOST_HEADER + "node01 ok - 8 1 1 0 0 0\n",
            ),
            membership_command: _result(
                membership_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            ),
        }
    )

    LsfCollector(runner=runner, user="demo").validate_selection(
        "normal", "node01"
    )

    assert [call for call, _timeout in runner.calls] == [
        queue_command,
        host_command,
        membership_command,
    ]


def test_live_selector_auto_validation_only_queries_queue() -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    runner = FakeRunner(
        {
            queue_command: _result(
                queue_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            )
        }
    )

    LsfCollector(runner=runner, user="demo").validate_selection("normal")

    assert [call for call, _timeout in runner.calls] == [queue_command]


def test_live_selector_rejects_valid_empty_host_membership() -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    host_command = ("bhosts", "-w")
    membership_command = (
        "bqueues", "-u", "demo", "-m", "node02", "-w",
    )
    runner = FakeRunner(
        {
            queue_command: _result(
                queue_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            ),
            host_command: _result(
                host_command,
                HOST_HEADER + "node02 ok - 8 0 0 0 0 0\n",
            ),
            membership_command: _command_result(
                membership_command,
                returncode=255,
                stderr=(
                    "node02: Host or host group is not used by the queue\n"
                ),
            ),
        }
    )

    with pytest.raises(
        CollectorError, match="no longer in the selected queue"
    ):
        LsfCollector(runner=runner, user="demo").validate_selection(
            "normal", "node02"
        )


@pytest.mark.parametrize(
    ("host_status", "membership_queue", "message"),
    (
        ("closed", "normal", "no longer available"),
        ("ok", "batch", "no longer in the selected queue"),
    ),
)
def test_live_selector_rejects_changed_host_state_or_membership(
    host_status: str, membership_queue: str, message: str
) -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    host_command = ("bhosts", "-w")
    membership_command = (
        "bqueues", "-u", "demo", "-m", "node01", "-w",
    )
    runner = FakeRunner(
        {
            queue_command: _result(
                queue_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            ),
            host_command: _result(
                host_command,
                HOST_HEADER + f"node01 {host_status} - 8 1 1 0 0 0\n",
            ),
            membership_command: _result(
                membership_command,
                QUEUE_HEADER
                + f"{membership_queue} 30 Open:Active - - - - 1 0 1 0\n",
            ),
        }
    )

    with pytest.raises(CollectorError, match=message):
        LsfCollector(runner=runner, user="demo").validate_selection(
            "normal", "node01"
        )
