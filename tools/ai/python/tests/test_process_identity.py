"""Linux identity extraction stays read-only and preserves caller error handling."""

import os

import pytest

from sico import process_identity as identity
from sico.service import background_watchdog as watchdog


def test_current_process_identity():
    current = identity.process_identity(os.getpid())
    assert current["pid"] == os.getpid()
    assert current["group"] == os.getpgrp()
    assert current["start"].isdigit()
    assert current["state"] in "RSDTtXZPI"
    assert identity.boot_id()


def test_process_name_with_spaces_and_parentheses(monkeypatch):
    class Stat:
        def __init__(self, *parts):
            assert parts == ("/proc", "123", "stat")

        def read_text(self):
            return "123 (worker (nested) name)) " + " ".join(
                ["S", "1", "45"] + ["0"] * 16 + ["987654"] + ["0"] * 5
            )

    monkeypatch.setattr(identity, "Path", Stat)
    assert identity.process_identity(123) == {
        "pid": 123, "start": "987654", "group": 45, "state": "S",
    }


def test_boot_identity_strips_proc_whitespace(monkeypatch):
    class Boot:
        def __init__(self, *parts):
            assert parts == ("/proc/sys/kernel/random/boot_id",)

        def read_text(self):
            return "  boot-fixture\n"

    monkeypatch.setattr(identity, "Path", Boot)
    assert identity.boot_id() == "boot-fixture"


@pytest.mark.parametrize("reader,args", [(identity.process_identity, (123,)),
                                        (identity.boot_id, ())])
@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_read_errors_remain_visible_to_the_caller(monkeypatch, reader, args, error):
    class Unreadable:
        def __init__(self, *parts):
            pass

        def read_text(self):
            raise error("unavailable identity evidence")

    monkeypatch.setattr(identity, "Path", Unreadable)
    with pytest.raises(error, match="unavailable identity evidence"):
        reader(*args)


def test_watchdog_exports_are_the_read_only_owner_objects():
    assert watchdog.process_identity is identity.process_identity
    assert watchdog.boot_id is identity.boot_id
