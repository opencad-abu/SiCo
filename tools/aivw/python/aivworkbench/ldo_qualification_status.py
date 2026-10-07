"""Qualification status and gate identities."""

from __future__ import annotations

M3_SCHEMA_VERSION = 1


M3_QUALIFIED = "QUALIFIED"


M3_ANALOG_ISLAND = "ANALOG_ISLAND"


M3_BLOCKED_INPUT = "BLOCKED_INPUT"


M3_BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"


M3_NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"


M3_FAIL = "FAIL_QUALIFICATION"


M3_FAIL_REPEAT = "FAIL_REPEAT"


M3_STALE_SOURCE = "STALE_SOURCE"


LDO_TOPOLOGIES = ("LDO_MASTER", "LDO_AON")


LDO_GATES = tuple("G%d" % value for value in range(8))


qualification_GATE_STATUS = frozenset(
    {
        "PASS",
        M3_ANALOG_ISLAND,
        M3_BLOCKED_INPUT,
        M3_BLOCKED_ENVIRONMENT,
        M3_NEEDS_MORE_EVIDENCE,
        M3_STALE_SOURCE,
        "FAIL",
        "FAIL_CORRELATION",
        "FAIL_STATIC",
        "FAIL_COMPILE",
        "FAIL_XCELIUM",
        "FAIL_SPECTRE",
        "FAIL_EVIDENCE",
    }
)
