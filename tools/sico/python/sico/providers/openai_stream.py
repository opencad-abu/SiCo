"""Assemble complete Chat Completions turns before dispatching any function tool."""

from __future__ import annotations

from ..core.contracts import ModelTurn, ProviderError, TextDelta, ToolCall
from ..transport.framing import strict_json


def merge_calls(buffers, pieces):
    if not isinstance(pieces, list) or len(pieces) > 32:
        raise ProviderError("Invalid tool delta list")
    for piece in pieces:
        if not isinstance(piece, dict):
            raise ProviderError("Invalid tool delta")
        index = piece.get("index")
        if type(index) is not int or not 0 <= index < 32:
            raise ProviderError("Invalid tool delta index")
        item = buffers.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if piece.get("type") not in (None, "function"):
            raise ProviderError("Only function tools are supported")
        call_id = piece.get("id")
        if call_id is not None:
            if not isinstance(call_id, str) or (item["id"] and item["id"] != call_id):
                raise ProviderError("Tool ID changed during streaming")
            item["id"] = call_id
        function = piece.get("function", {})
        if not isinstance(function, dict):
            raise ProviderError("Invalid function delta")
        for field in ("name", "arguments"):
            value = function.get(field, "")
            if not isinstance(value, str):
                raise ProviderError("Invalid function delta text")
            item[field] += value


def parse_stream(events):
    text, reasoning, buffers, usage = "", "", {}, {}
    finish, done = None, False
    for event in events:
        if done:
            raise ProviderError("Model data arrived after stream completion")
        if event is None:
            done = True
            continue
        if not isinstance(event, dict) or "error" in event:
            raise ProviderError("Model stream returned an error")
        if event.get("usage") is not None:
            reported = event["usage"]
            if not isinstance(reported, dict):
                raise ProviderError("Invalid model token usage")
            for field in ("prompt_tokens", "completion_tokens"):
                value = reported.get(field, 0)
                if type(value) is not int or value < 0:
                    raise ProviderError("Invalid model token usage")
                usage[field] = value
        choices = event.get("choices")
        if choices == [] and event.get("usage") is not None:
            continue
        if not isinstance(choices, list) or len(choices) != 1:
            raise ProviderError("Expected one streamed model choice")
        choice = choices[0]
        if (
            not isinstance(choice, dict)
            or type(choice.get("index")) is not int
            or choice["index"] != 0
            or finish is not None
        ):
            raise ProviderError("Invalid or late model choice")
        delta = choice.get("delta")
        if not isinstance(delta, dict) or delta.get("role") not in (None, "assistant"):
            raise ProviderError("Invalid model delta")
        if delta.get("refusal") or delta.get("function_call"):
            raise ProviderError("Model refused or returned an unsupported legacy function call")
        content, thought = delta.get("content"), delta.get("reasoning_content")
        if any(value is not None and not isinstance(value, str) for value in (content, thought)):
            raise ProviderError("Model delta must contain text")
        text += content or ""
        reasoning += thought or ""
        if delta.get("tool_calls") is not None:
            merge_calls(buffers, delta["tool_calls"])
        if (
            len(text)
            + len(reasoning)
            + sum(len(value) for item in buffers.values() for value in item.values())
            > 128_000
        ):
            raise ProviderError("Model content exceeds output budget")
        if content:
            yield TextDelta(content)
        if choice.get("finish_reason") is not None:
            finish = choice["finish_reason"]
            if not isinstance(finish, str) or finish not in {"stop", "tool_calls"}:
                raise ProviderError("Model stopped without a complete usable turn")
    if not done or finish is None:
        raise ProviderError("Model stream ended before complete finish and [DONE]")
    if bool(buffers) != (finish == "tool_calls") or sorted(buffers) != list(range(len(buffers))):
        raise ProviderError("Tool calls do not match model finish reason or indexes")
    calls = tuple(
        ToolCall(item["id"], item["name"], strict_json(item["arguments"]))
        for _, item in sorted(buffers.items())
    )
    for call in calls:
        call.record()
    if len({call.id for call in calls}) != len(calls):
        raise ProviderError("Model reused a tool call ID")
    blocks = ({"type": "openai_reasoning", "reasoning_content": reasoning},) if reasoning else ()
    yield ModelTurn(
        text,
        calls,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        blocks,
    )
