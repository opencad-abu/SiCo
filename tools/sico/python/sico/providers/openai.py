"""Chat Completions function tools, adapted from aDesigner's message mapping."""

from __future__ import annotations

import json

from .http_stream import sse_events
from .openai_stream import parse_stream
from cadai.provider_settings import provider_endpoint, validate_key, validate_settings


def chat_messages(system, messages):
    result = [{"role": "system", "content": system}]
    for message in messages:
        role = message["role"]
        item = {"role": role, "content": message["content"]}
        if role == "tool":
            item["tool_call_id"] = message["tool_call_id"]
        elif role == "assistant":
            calls = message.get("tool_calls", [])
            if calls:
                item["content"] = message["content"] or None
                item["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["input"], ensure_ascii=False),
                        },
                    }
                    for call in calls
                ]
            for block in message.get("provider_blocks", []):
                if block.get("type") == "openai_reasoning":
                    item["reasoning_content"] = block["reasoning_content"]
        result.append(item)
    return result


class OpenAIProvider:
    def __init__(
        self,
        base_url,
        api_key,
        model,
        *,
        max_tokens=4096,
        timeout=30,
        token_limit_field="max_tokens",
        include_usage=False,
    ):
        self.endpoint = provider_endpoint(base_url, "chat/completions")
        validate_settings(model, max_tokens, timeout)
        validate_key(api_key)
        if token_limit_field not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("Invalid OpenAI token limit field")
        if type(include_usage) is not bool:
            raise ValueError("OpenAI include_usage must be a boolean")
        self.api_key, self.model = api_key, model
        self.max_tokens, self.timeout = max_tokens, timeout
        self.token_limit_field, self.include_usage = token_limit_field, include_usage
        self.base = "openai"
        self.label = f"OpenAI Chat Completions / {model}"

    def stream(self, system, messages, tools, cancelled):
        payload = {
            "model": self.model,
            "messages": chat_messages(system, messages),
            "stream": True,
            self.token_limit_field: self.max_tokens,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in tools
            ]
            payload["tool_choice"] = "auto"
        if self.include_usage:
            payload["stream_options"] = {"include_usage": True}
        source = sse_events(
            self.endpoint,
            {"authorization": "Bearer " + self.api_key},
            payload,
            self.timeout,
            cancelled,
            done_marker=True,
        )
        try:
            yield from parse_stream(source)
        finally:
            source.close()
