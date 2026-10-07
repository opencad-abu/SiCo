"""Request-scoped, reversible tool identities; never rewrite argument or result data."""

from __future__ import annotations

import hashlib
import json
import re

from .json_values import json_copy


class CompatibilityError(ValueError):
    pass


class ToolNames:
    def __init__(self):
        self.identities = {}
        self.aliases = {}
        self.call_aliases = {}

    def encode(self, namespace, name):
        if not isinstance(name, str) or not name:
            raise CompatibilityError("Tool name must be a nonempty string")
        if namespace is not None and (not isinstance(namespace, str) or not namespace):
            raise CompatibilityError("Invalid tool namespace")
        identity = (namespace, name)
        if identity in self.identities:
            return self.identities[identity]
        alias = namespace + "__" + name if namespace else name
        if len(alias) > 64 or not re.fullmatch(r"[a-zA-Z0-9_-]+", alias):
            digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
            alias = "cad_tool_" + digest
        if alias in self.aliases and self.aliases[alias] != identity:
            raise CompatibilityError("Flattened tool names collide; rename conflicting tools")
        if len(self.aliases) >= 4096:
            raise CompatibilityError("Too many tool identities")
        self.identities[identity] = alias
        self.aliases[alias] = identity
        return alias

    def definitions(self, definitions):
        if not isinstance(definitions, list):
            raise CompatibilityError("Tool definitions must be an array")
        result = []
        declared = set()
        for definition in definitions:
            tool = json_copy(definition)
            if not isinstance(tool, dict):
                raise CompatibilityError("Invalid tool definition")
            if tool.get("type") == "namespace":
                namespace = tool.get("name")
                if not isinstance(namespace, str) or not namespace:
                    raise CompatibilityError("Invalid tool namespace")
                children = tool.get("tools")
                if not isinstance(children, list):
                    raise CompatibilityError("Namespace tools must be an array")
                for child in children:
                    if not isinstance(child, dict) or child.get("type") not in {
                        "function",
                        "custom",
                    }:
                        raise CompatibilityError("Unsupported nested namespace tool")
                    child["name"] = self.encode(namespace, child.get("name"))
                    child.pop("namespace", None)
                    description = tool.get("description", "")
                    if description:
                        child["description"] = description + "\n" + child.get("description", "")
                    result.append(child)
            elif tool.get("type") in {"function", "custom"}:
                tool["name"] = self.encode(tool.pop("namespace", None), tool.get("name"))
                result.append(tool)
            else:
                # Other wire tool types retain their semantics and provider validation.
                result.append(tool)
        for tool in result:
            if tool.get("type") in {"function", "custom"}:
                name = tool["name"]
                if name in declared:
                    raise CompatibilityError("Duplicate tool declaration")
                declared.add(name)
        return result

    def item(self, item, *, incoming=False):
        if not isinstance(item, dict):
            raise CompatibilityError("Invalid Responses item")
        item = json_copy(item)
        kind = item.get("type")
        if kind in {"function_call_output", "custom_tool_call_output"} and item.get("name") is None:
            item.pop("name", None)
            if item.get("namespace"):
                raise CompatibilityError("Named tool output requires a tool name")
            item.pop("namespace", None)
        if (
            kind
            in {
                "function_call",
                "custom_tool_call",
                "function_call_output",
                "custom_tool_call_output",
            }
            and "name" in item
        ):
            if incoming:
                name = item.get("name")
                if not isinstance(name, str) or name not in self.aliases:
                    raise CompatibilityError("Model returned an unknown flattened tool name")
                namespace, original = self.aliases[name]
                item["name"] = original
                if namespace:
                    item["namespace"] = namespace
                else:
                    item.pop("namespace", None)
            else:
                if kind == "custom_tool_call_output" and item.get("call_id") in self.call_aliases:
                    item["name"] = self.call_aliases[item["call_id"]]
                    item.pop("namespace", None)
                else:
                    item["name"] = self.encode(item.pop("namespace", None), item["name"])
                if kind in {"function_call", "custom_tool_call"} and item.get("call_id"):
                    self.call_aliases[item["call_id"]] = item["name"]
        if kind in {"tool_search_output", "additional_tools"} and "tools" in item:
            if incoming:
                raise CompatibilityError("Server-side tool discovery is unsupported in flat mode")
            item["tools"] = self.definitions(item["tools"])
        return item

    def choice(self, choice):
        if not isinstance(choice, dict):
            return choice
        choice = json_copy(choice)
        if choice.get("type") in {"function", "custom"}:
            choice["name"] = self.encode(choice.pop("namespace", None), choice.get("name"))
        elif choice.get("type") == "allowed_tools":
            choice["tools"] = [self.choice(t) for t in choice.get("tools", [])]
        elif choice.get("type") == "namespace":
            raise CompatibilityError("Namespace-only tool choice is unsupported in flat mode")
        return choice

    def request(self, request):
        request = json_copy(request)
        if "tools" in request:
            request["tools"] = self.definitions(request["tools"])
        if isinstance(request.get("input"), list):
            request["input"] = [self.item(item) for item in request["input"]]
        if "tool_choice" in request:
            request["tool_choice"] = self.choice(request["tool_choice"])
        return request

    def response(self, response):
        response = json_copy(response)
        if isinstance(response.get("output"), list):
            response["output"] = [self.item(item, incoming=True) for item in response["output"]]
        return response

    def event(self, event):
        event = json_copy(event)
        if event.get("type") in {"response.output_item.added", "response.output_item.done"}:
            event["item"] = self.item(event["item"], incoming=True)
        if isinstance(event.get("response"), dict):
            event["response"] = self.response(event["response"])
        return event
