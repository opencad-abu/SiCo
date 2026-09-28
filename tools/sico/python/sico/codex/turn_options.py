"""Public model inventory and submission-frozen turn controls."""

import json
import time
from copy import deepcopy

from ..storage.input_assets import text_value
from . import access_policy

FIELDS = {"model", "effort", "mode", "serviceTier", "serviceTierForTurn", "outputSchema", "access"}


def parse_output_schema(text):
    from ..transport.framing import strict_json

    if not text.strip():
        return None
    if len(text.encode("utf-8")) > 32768:
        raise ValueError("Output schema exceeds 32 KiB")
    wrapped = strict_json('{"schema":' + text + '}')
    if set(wrapped) != {"schema"}:
        raise ValueError("Output schema must be one JSON value")
    value = wrapped["schema"]
    if not isinstance(value, (dict, bool)):
        raise ValueError("Output schema must be a JSON object or boolean")
    json.dumps(value, allow_nan=False)
    return value


class TurnOptions:
    def __init__(self, backend, records):
        self.backend = backend
        records = list(records)
        self.selected = {"model": getattr(backend.provider, "model", ""), "mode": "default",
                         "serviceTier": "default", "access": access_policy.default_access(
                             legacy=any(e["kind"] == "codex.thread" for e in records))}
        for event in records:
            if event["kind"] == "codex.thread" and "access" in event["payload"]:
                self.selected["access"] = access_policy.validate(event["payload"]["access"])
            if (event["kind"] == "codex.turn.settings"
                    and event["payload"].get("status") == "accepted"):
                restored = self.sticky(event["payload"]["options"])
                restored.setdefault("access", self.selected["access"])
                restored["access"] = access_policy.validate(restored["access"])
                self.selected = restored
            elif event["kind"] == "codex.access.configured":
                self.selected["access"] = access_policy.validate(event["payload"]["access"])
        self.catalog = {"models": [], "modes": [], "skills": []}

    @staticmethod
    def sticky(options):
        return {key: value for key, value in options.items()
                if key not in {"serviceTierForTurn", "outputSchema"}}

    def status(self, busy=False):
        if not busy:
            self.backend._ensure_runtime()
            rpc, deadline = self.backend.runtime.rpc, time.monotonic() + 10

            def request(method, params):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Input inventory time budget exceeded")
                return rpc.request(method, params, timeout=min(3, remaining))

            models, cursor, seen = [], None, set()
            for _ in range(10):
                page = request("model/list", {"cursor": cursor, "limit": 100})
                models.extend(bounded_rows(page["data"], 100))
                cursor = page.get("nextCursor")
                if cursor is None:
                    break
                text_value(cursor, 4096)
                if cursor in seen:
                    raise ValueError("Repeated model inventory cursor")
                seen.add(cursor)
            else:
                raise ValueError("Model inventory exceeds page budget")
            modes = bounded_rows(request("collaborationMode/list", {})["data"], 32)
            skills = bounded_rows(request("skills/list", {
                "cwds": [self.backend.runtime.cwd]})["data"], 32)
            self.catalog = {
                "models": [model_row(m) for m in models if not m.get("hidden")],
                "modes": [m["mode"] for m in modes if m.get("mode") in {"default", "plan"}],
                "skills": [skill_row(s) for group in skills
                           for s in bounded_rows(group["skills"], 512)],
            }
            if len(self.catalog["skills"]) > 512:
                self.catalog = {"models": [], "modes": [], "skills": []}
                raise ValueError("Skill inventory exceeds item budget")
        return deepcopy({**self.catalog, "selected": self.selected, "busy": busy})

    def freeze(self, options=None):
        if options is None:
            return deepcopy(self.selected)
        if not isinstance(options, dict) or set(options) - FIELDS:
            raise ValueError("Unsupported turn setting")
        value = deepcopy(options)
        value.setdefault("model", self.selected["model"])
        value.setdefault("mode", self.selected.get("mode", "default"))
        # An explicit selection is a complete snapshot, including clearing a sticky tier.
        value.setdefault("serviceTier", "default")
        value["access"] = access_policy.validate(value.get("access", self.selected["access"]))
        model = value["model"]
        if model or getattr(self.backend.provider, "model", ""):
            model = text_value(model, 256)
        elif model != "":
            raise ValueError("Invalid model")
        row = next((m for m in self.catalog["models"] if m["model"] == model), None)
        if row is None and model != getattr(self.backend.provider, "model", ""):
            raise ValueError("Model is not in this runtime's advertised inventory")
        if text_value(value["mode"], 64) not in {"default", "plan"} or (
                value["mode"] == "plan" and "plan" not in self.catalog["modes"]):
            raise ValueError("Collaboration mode is unavailable")
        if "effort" in value:
            if value["effort"] is None:
                value.pop("effort")
        if "effort" in value:
            effort = text_value(value["effort"], 64)
            if not row or effort not in {e["reasoningEffort"]
                                         for e in row["supportedReasoningEfforts"] or []}:
                raise ValueError("Reasoning effort is not advertised for this model")
        for key in ("serviceTier", "serviceTierForTurn"):
            if key in value:
                tier = text_value(value[key], 64)
                if tier != "default" and (not row or tier not in {
                        t["id"] for t in row["serviceTiers"] or []}):
                    raise ValueError("Service tier is not advertised for this model")
        if "outputSchema" in value:
            value["outputSchema"] = parse_output_schema(json.dumps(value["outputSchema"],
                                                                    allow_nan=False))
        return value

    def params(self, value):
        if not value:
            return {}
        value = self.freeze(value)
        if not value.get("model"):
            return access_policy.turn_params(value["access"], interactive=hasattr(self.backend, "audit"))
        return {**{k: v for k, v in value.items() if k not in {"mode", "access"}},
                **access_policy.turn_params(value["access"], interactive=hasattr(self.backend, "audit")),
                "collaborationMode": {
            "mode": value["mode"], "settings": {"model": value["model"],
                "reasoning_effort": value.get("effort"), "developer_instructions": None}}}

    def accepted(self, value):
        selected = deepcopy(self.sticky(value))
        selected.setdefault("access", self.selected["access"])
        self.selected = selected

    def configure_access(self, rpc, thread_id, value):
        """Operations without turn/start still honor their queued scope snapshot."""
        access = access_policy.validate((value or self.selected)["access"])
        access_policy.configure_thread(rpc, thread_id, access, interactive=hasattr(self.backend, "audit"))
        self.backend._event("codex.access.configured", {"access": access})
        self.selected["access"] = access

    def needs_catalog(self, value):
        return bool(value and (value.get("model") != getattr(self.backend.provider, "model", "")
                    or value.get("mode") == "plan" or value.get("effort") is not None
                    or any(value.get(key, "default") != "default"
                           for key in ("serviceTier", "serviceTierForTurn"))))

    def check_inputs(self, inputs, options):
        bounded_rows(inputs, 8)
        model = (options or self.selected).get("model")
        row = next((m for m in self.catalog["models"] if m["model"] == model), {})
        supported = row.get("inputModalities") or []
        for part in inputs:
            kind = text_value(part.get("type"), 64)
            modality = {"image": "image", "localImage": "image",
                        "audio": "audio", "localAudio": "audio"}.get(kind)
            if modality and modality not in supported:
                raise ValueError("Selected model does not advertise " + modality + " input")


def bounded_rows(rows, limit):
    if not isinstance(rows, list) or len(rows) > limit or any(
            not isinstance(row, dict) for row in rows):
        raise ValueError("Invalid or oversized input inventory")
    return rows


def model_row(row):
    model = text_value(row.get("model"), 256)
    efforts = bounded_rows(row.get("supportedReasoningEfforts") or [], 32)
    tiers = bounded_rows(row.get("serviceTiers") or [], 32)
    modalities = row.get("inputModalities") or []
    if not isinstance(modalities, list) or len(modalities) > 16:
        raise ValueError("Invalid model input modalities")
    return {"model": model, "displayName": text_value(row.get("displayName") or model, 256),
            "inputModalities": [m for m in modalities if m in ("text", "image", "audio")],
            "supportedReasoningEfforts": [
                {"reasoningEffort": text_value(e.get("reasoningEffort"), 64)} for e in efforts],
            "defaultReasoningEffort": row.get("defaultReasoningEffort"),
            "serviceTiers": [{"id": text_value(t.get("id"), 64),
                              "name": text_value(t.get("name"), 128)} for t in tiers]}


def skill_row(row):
    if type(row.get("enabled")) is not bool:
        raise ValueError("Invalid skill availability")
    return {"name": text_value(row.get("name"), 128),
            "path": text_value(row.get("path")), "enabled": row["enabled"]}
