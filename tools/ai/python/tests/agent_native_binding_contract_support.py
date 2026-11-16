# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""Shared agent native binding contract fixtures and helpers."""

from __future__ import annotations

from test_agent_session_bindings import target


def parameters(function="aiCreateExecute"):
    return dict(
        function=function,
        values=["prepare", "digest", "operation"],
        claim=["session", "create-execute:operation", "digest"],
        expected=[
            "task:test",
            "foreground",
            ["Work", "Amp", "schematic"],
            "schematic",
            "digest",
            "/worklib",
            None,
        ],
    )


def output():
    return target("created", window="window:2", library_path="/worklib")


def created_message(identity, context=None, **extra):
    return dict(
        kind="binding.native_created",
        request_id=identity["request_id"],
        session_id=identity["session_id"],
        binding_id=identity["binding_id"],
        context=(context or output()).record(),
        created_by_workflow=True,
        verified=True,
        **extra,
    )


def dispatch(broker, function="aiCreateExecute", request_id="a" * 32):
    return broker.native_bindings.dispatch(
        "owner",
        target(),
        "circuit_call",
        parameters(function),
        request_id,
        "task",
        [["Work", "Amp", "schematic", "/worklib"]],
    )
