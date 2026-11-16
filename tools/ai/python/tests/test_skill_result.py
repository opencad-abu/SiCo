"""Shared SKILL result boundaries: one capture record, stable failures and budgets."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from cadai.mcp import McpServer
from cadai.skill_diagnostics import SkillDiagnostics, carry_output, output_fields, record_output
from cadai.skill_result import call_skill, decode_skill_result


def captured(text, truncated=False):
    return dict(output=text, output_truncated=truncated, output_capture="skill_ports")


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("kind", ["success", "business_error", "transport_error", "invalid_json"])
def test_call_once_records_each_reply_without_changing_failure_meaning(native, kind):
    calls = []
    replies = {
        "success": (True, dict(value='{"ok":true,"item":1}')),
        "business_error": (True, dict(value='{"ok":false,"code":"device_failed"}')),
        "transport_error": (False, dict(code="connection_lost", data=captured("warning\n"))),
        "invalid_json": (True, dict(value="broken")),
    }
    expected_ok, reply = replies[kind]
    if kind != "transport_error":
        reply.update(captured("warning\n"))

    def invoke(method, arguments):
        calls.append((method, arguments))
        return expected_ok, reply

    with SkillDiagnostics() as diagnostics:
        ok, result = call_skill(SimpleNamespace(call=invoke), "aiRead()", native=native)
        converted = carry_output({"converted": True}, result)
        assert diagnostics.attach(converted)["output"] == "warning\n"
        # Identical messages from separate calls must each survive; never dedup text.
        call_skill(SimpleNamespace(call=invoke), "aiRead()", native=native)
        assert diagnostics.attach(converted)["output"] == "warning\nwarning\n"
    assert calls == [("eval_skill_native" if native else "eval_skill", {"code": "aiRead()"})] * 2
    assert ok is (kind == "success")
    if kind != "success":
        assert result["code"] == dict(business_error="device_failed", transport_error="connection_lost",
                                     invalid_json="invalid_result")[kind]


def test_quiet_envelope_preserves_producer_output_and_business_success():
    value = dict(ok=True, **captured("*Error* text is evidence, not the success flag\n"))
    with SkillDiagnostics() as diagnostics:
        ok, result = decode_skill_result(dict(value=json.dumps(value), **captured("")))
        assert ok and result == value
        assert diagnostics.attach({})["output"] == value["output"]


def test_raised_transport_error_keeps_identity_and_records_its_diagnostics():
    class NativeFailure(RuntimeError):
        data = dict(code="outcome_unknown", **captured("*Error* native failure\n"))

    failure = NativeFailure("original")

    def invoke(*_):
        raise failure

    with SkillDiagnostics() as diagnostics:
        with pytest.raises(NativeFailure) as caught:
            call_skill(SimpleNamespace(call=invoke), "aiRead()", native=True)
        assert caught.value is failure
        assert diagnostics.attach({}) == captured("*Error* native failure\n")


def test_outer_capture_owns_text_without_duplicating_inner_echo():
    inner = dict(ok=False, code="original", **captured("warning\n"))
    with SkillDiagnostics() as diagnostics:
        ok, result = decode_skill_result(dict(value=json.dumps(inner), **captured("warning\n")))
        assert not ok and result == inner
        assert diagnostics.attach({})["output"] == "warning\n"


def test_output_contract_is_explicit_and_legacy_envelopes_do_not_recurse_forever():
    arbitrary = dict(output="business output", value='{"ok":true}')
    with SkillDiagnostics() as diagnostics:
        ok, result = decode_skill_result(arbitrary)
        assert ok and diagnostics.attach(result) == {"ok": True}
    legacy = {"data": {"data": captured("old bridge")}}
    assert output_fields(legacy) == captured("old bridge")
    cyclic = {}
    cyclic["data"] = cyclic
    assert output_fields(cyclic) == {}


def test_truncated_source_stops_the_aggregate_at_the_first_gap():
    with SkillDiagnostics() as diagnostics:
        record_output(captured("before\n"))
        record_output(captured("partial", True))
        record_output(captured("later\n"))
        assert diagnostics.attach({}) == captured("before\npartial", True)


def test_async_requests_and_exception_cleanup_are_isolated():
    async def worker(marker):
        with SkillDiagnostics() as outer:
            record_output(captured(marker))
            await asyncio.sleep(0)
            with pytest.raises(RuntimeError):
                with SkillDiagnostics():
                    record_output(captured("nested"))
                    raise RuntimeError("failed")
            record_output(captured(marker))
            return outer.attach({})["output"]

    async def run():
        return await asyncio.gather(worker("A"), worker("B"))

    assert asyncio.run(run()) == ["AA", "BB"]
    with SkillDiagnostics() as following:
        assert following.attach({}) == {}


def test_public_raw_eval_keeps_guard_diagnostics_and_arbitrary_value():
    server = McpServer(SimpleNamespace(call=lambda *_: (True, dict(value="42", **captured("EVAL\n")))))

    def guard(_):
        record_output(captured("GUARD\n"))

    server.pdk_selection_guard = guard
    response = server.handle(dict(jsonrpc="2.0", id=1, method="tools/call",
                                  params=dict(name="eval_skill", arguments=dict(code="42"))))
    result = json.loads(response["result"]["content"][0]["text"])
    assert not response["result"]["isError"]
    assert result["value"] == "42" and result["output"] == "GUARD\nEVAL\n"


def test_relay_uses_the_same_escaped_output_budget():
    from sico.transport.relay import circuit_diagnostic

    text = "\x01\n\t" * 22000
    result = circuit_diagnostic(dict(diagnostic=dict(code="original", **captured(text))))
    assert result["code"] == "original" and result["output_truncated"]
    assert result["diagnostic_truncated"] and text.startswith(result["output"])
    assert len(json.dumps(result["output"], ensure_ascii=False).encode()) <= 80000
