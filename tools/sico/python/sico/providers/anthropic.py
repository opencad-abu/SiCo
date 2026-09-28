"""Anthropic Messages adapter; protocol blocks stay separate from public GUI events."""

from __future__ import annotations

import json

from ..core.contracts import ModelTurn, ProviderError, TextDelta, ToolCall, json_copy
from ..transport.framing import strict_json
from .http_stream import sse_events
from cadai.provider_settings import provider_endpoint, validate_key, validate_settings


def _messages(messages):
    result = []
    for message in messages:
        role = message["role"]
        if role == "user":
            content = [{"type": "text", "text": message["content"]}]
        elif role == "assistant":
            content = json_copy(
                [
                    b
                    for b in message.get("provider_blocks", [])
                    if b.get("type") != "openai_reasoning"
                ]
            )
            if not content:
                if message["content"]:
                    content.append({"type": "text", "text": message["content"]})
                content.extend(
                    {"type": "tool_use", **call} for call in message.get("tool_calls", [])
                )
            if not content:
                continue
        else:
            role = "user"
            parsed = json.loads(message["content"])
            content = [
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message["content"],
                    "is_error": parsed.get("status") != "ok",
                }
            ]
        if result and result[-1]["role"] == role:
            result[-1]["content"].extend(content)
        else:
            result.append({"role": role, "content": content})
    return result


class AnthropicProvider:
    def __init__(self, base_url, api_key, model, *, max_tokens=4096, timeout=30):
        self.endpoint = provider_endpoint(base_url, "messages")
        validate_settings(model, max_tokens, timeout)
        validate_key(api_key)
        self.api_key, self.model = api_key, model
        self.max_tokens, self.timeout = max_tokens, timeout
        self.base = "anthropic"
        self.label = f"Anthropic / {model}"

    def stream(self, system, messages, tools, cancelled):
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": _messages(messages),
            "stream": True,
        }
        if tools:
            # MCP effect annotations belong to the local registry/bridge, not Messages API.
            payload["tools"] = [
                {key: json_copy(tool[key]) for key in ("name", "description", "input_schema")}
                for tool in tools
            ]
        source = sse_events(
            self.endpoint,
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            payload,
            self.timeout,
            cancelled,
        )
        try:
            yield from parse_stream(source)
        finally:
            source.close()


def parse_stream(events):
    blocks, closed, inputs, usage = {}, set(), {}, {}
    text, started, ended, stop_reason, seen = "", False, False, None, False
    for event in events:
        kind = event.get("type")
        if ended:
            raise ProviderError("Model data arrived after message_stop")
        if kind == "ping":
            continue
        if kind == "error":
            retryable = event.get("error", {}).get("type") in {
                "overloaded_error",
                "rate_limit_error",
            }
            raise ProviderError("Model stream returned an error", retryable=retryable and not seen)
        if kind == "message_start":
            if started:
                raise ProviderError("Duplicate message_start")
            started = True
            usage.update(event.get("message", {}).get("usage", {}))
            continue
        if not started:
            raise ProviderError("Model event before message_start")
        seen = True
        if kind == "content_block_start":
            index = event.get("index")
            if type(index) is not int or index != len(blocks) or len(blocks) >= 32:
                raise ProviderError("Invalid model content block index")
            block = json_copy(event.get("content_block", {}))
            if block.get("type") not in {"text", "tool_use", "thinking", "redacted_thinking"}:
                raise ProviderError("Unsupported model content block")
            # Anthropic may include an initial text/thinking value here and then
            # repeat it in deltas. Accumulate protocol state but publish deltas only.
            if block.get("type") in {"text", "thinking"}:
                block[block["type"]] = ""
            if block.get("type") == "thinking":
                block["signature"] = ""
            blocks[index] = block
        elif kind == "content_block_delta":
            index, delta = event.get("index"), event.get("delta", {})
            if index not in blocks or index in closed:
                raise ProviderError("Delta for an absent or closed block")
            block = blocks[index]
            dtype = delta.get("type")
            fields = {
                "text_delta": ("text", "text"),
                "thinking_delta": ("thinking", "thinking"),
                "signature_delta": ("thinking", "signature"),
                "input_json_delta": ("tool_use", "partial_json"),
            }
            if dtype not in fields or block["type"] != fields[dtype][0]:
                raise ProviderError("Model delta does not match block type")
            field = fields[dtype][1]
            value = delta.get(field)
            if not isinstance(value, str):
                raise ProviderError("Model delta must contain text")
            if dtype == "input_json_delta":
                inputs[index] = inputs.get(index, "") + value
            else:
                block[field] = block.get(field, "") + value
            if dtype == "text_delta":
                text += value
                yield TextDelta(value)
            if len(json.dumps(blocks)) + sum(map(len, inputs.values())) > 128_000:
                raise ProviderError("Model content exceeds output budget")
        elif kind == "content_block_stop":
            index = event.get("index")
            if index not in blocks or index in closed:
                raise ProviderError("Invalid content_block_stop")
            closed.add(index)
            if index in inputs:
                blocks[index]["input"] = strict_json(inputs[index])
        elif kind == "message_delta":
            stop_reason = event.get("delta", {}).get("stop_reason")
            usage.update(event.get("usage", {}))
        elif kind == "message_stop":
            if len(closed) != len(blocks) or stop_reason not in {
                "end_turn",
                "tool_use",
                "stop_sequence",
            }:
                raise ProviderError("Model stopped without a complete usable turn")
            ended = True
        else:
            raise ProviderError("Unsupported model event")
    if not ended:
        raise ProviderError("Model stream ended before message_stop")
    calls = tuple(
        ToolCall(b["id"], b["name"], b["input"]) for b in blocks.values() if b["type"] == "tool_use"
    )
    if bool(calls) != (stop_reason == "tool_use"):
        raise ProviderError("Tool calls do not match model stop reason")
    yield ModelTurn(
        text,
        calls,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        tuple(blocks.values()),
    )
