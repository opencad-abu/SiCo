"""Gateway-owned model catalog for one Codex session runtime.

The picker must offer exactly the models the configured provider URL serves.
Codex 0.156.1 only advertises its bundled OpenAI catalog for custom providers,
so this module reads ``{provider root}/models`` and hands the pinned runtime an
authoritative ``model_catalog_json``. Listing requests are bounded, the key
never follows a redirect, and a failed listing falls back to the configured
model alone instead of the bundled catalog. The harness template is re-derived
from the pinned binary so model instructions stay version-consistent and never
live in this repository. A listing row may declare optional
``supported_reasoning_levels``, ``default_reasoning_level`` and
``input_modalities``, plus ``service_tiers``. The gateway may instead declare
``effort: {supported_levels, default_level}`` and ``context_window``. When a
gateway omits reasoning levels, the configured standard fallback is used so
the picker still offers the provider's known effort values.
"""

from __future__ import annotations

import copy
import http.client
import json
import logging
import os
import subprocess
import urllib.error
import urllib.request

from cadai.codex_selection import probe_environment

from ..storage.input_assets import text_value
from cadai.json_values import strict_json

CATALOG_FILE = "model_catalog.json"
CATALOG_TIMEOUT = 5.0
CATALOG_BYTE_LIMIT = 262_144
CATALOG_MODEL_LIMIT = 100
BUNDLED_TIMEOUT = 10.0
BUNDLED_BYTE_LIMIT = 4 * 1024 * 1024
REASONING_EFFORTS_ENV = "SICO_REASONING_EFFORTS"
DEFAULT_REASONING_EFFORTS = ("low", "medium", "high", "max")

IDENTIFIER_KEYS = ("id", "slug", "model")
DISPLAY_KEYS = ("display_name", "name")
LISTING_SHAPES = ("data", "models")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A credential-bearing listing request never follows a redirect."""

    def redirect_request(self, *args, **kwargs):
        return None


def prepare(settings, home, executable):
    """Write this session's catalog and return a bounded, credential-free report."""
    models, reason = _gateway_models(settings)
    source = "gateway"
    if not models:
        source = "fallback"
        models = [_plain_spec(settings.model)]
    fallback = _fallback_reasoning_levels(settings.environment)
    models = _with_reasoning_fallback(models, fallback)
    try:
        template = _template(executable, settings)
    except (OSError, ValueError, subprocess.SubprocessError):
        logging.getLogger(__name__).warning(
            "Pinned Codex model template is unavailable; keeping the runtime default catalog")
        return {"path": None, "source": "none", "models": 0, "reason": "template_unavailable"}
    entries = _entries(models, template, settings.model, fallback)
    path = home / CATALOG_FILE
    try:
        _write(path, entries)
    except OSError:
        logging.getLogger(__name__).warning(
            "Session model catalog could not be written; keeping the runtime default catalog")
        return {"path": None, "source": "none", "models": 0, "reason": "write_failed"}
    if source == "fallback":
        logging.getLogger(__name__).warning(
            "Provider model listing unavailable (%s); the catalog keeps only the configured model",
            reason)
    return {"path": path, "source": source, "models": len(entries), "reason": reason}


def _fallback_reasoning_levels(environment):
    """Standard effort values used only when the gateway omits its own list."""
    raw = environment.get(REASONING_EFFORTS_ENV)
    if raw is None:
        values = DEFAULT_REASONING_EFFORTS
    elif not raw.strip() or raw.strip().lower() in {"none", "off"}:
        return []
    else:
        values = tuple(value.strip() for value in raw.split(",") if value.strip())
        if not values or len(values) > 32:
            raise ValueError("Invalid reasoning effort fallback")
    return [{"effort": text_value(value, 64), "description": ""}
            for value in dict.fromkeys(values)]


def _with_reasoning_fallback(models, fallback):
    if not fallback:
        return models
    result = []
    for model in models:
        if not model["reasoning_levels"]:
            model = copy.deepcopy(model)
            model["reasoning_levels"] = copy.deepcopy(fallback)
        result.append(model)
    return result


def _gateway_models(settings):
    """Return ``(models, reason)`` for the configured provider URL."""
    try:
        raw = _fetch(settings)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        return [], "http_" + str(code)
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        return [], "unreachable"
    except ValueError:
        return [], "invalid"
    try:
        models = _rows(strict_json(raw))
    except ValueError:
        return [], "invalid"
    if not models:
        return [], "empty"
    return models, ""


def _fetch(settings):
    request = urllib.request.Request(
        settings.models_endpoint,
        headers={"accept": "application/json",
                 "authorization": "Bearer " + settings.api_key},
        method="GET",
    )
    # System proxy and CA settings are honored; the key never follows a redirect.
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=CATALOG_TIMEOUT) as response:
        raw = response.read(CATALOG_BYTE_LIMIT + 1)
    if len(raw) > CATALOG_BYTE_LIMIT:
        raise ValueError("Model listing exceeds the byte limit")
    return raw


def _rows(payload):
    rows = None
    for shape in LISTING_SHAPES:
        candidate = payload.get(shape)
        if isinstance(candidate, list):
            rows = candidate
            break
    if rows is None or len(rows) > CATALOG_MODEL_LIMIT:
        raise ValueError("Invalid model listing")
    models, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifier = _identifier(row)
        if identifier is None or identifier in seen:
            continue
        seen.add(identifier)
        models.append({"model": identifier,
                       "display_name": _display(row, identifier),
                       "description": _description(row),
                       "reasoning_levels": _reasoning_levels(row),
                       "default_reasoning_level": _default_reasoning_level(row),
                       "input_modalities": _input_modalities(row),
                       "service_tiers": _service_tiers(row),
                       "context_window": _context_window(row)})
    return models


def _identifier(row):
    for key in IDENTIFIER_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            try:
                return text_value(value, 256)
            except ValueError:
                continue
    return None


def _display(row, identifier):
    for key in DISPLAY_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            try:
                return text_value(value, 256)
            except ValueError:
                break
    return identifier


def _description(row):
    value = row.get("description")
    if isinstance(value, str) and value.strip():
        try:
            return text_value(value, 1024)
        except ValueError:
            return None
    return None


def _reasoning_levels(row):
    if "supported_reasoning_levels" in row:
        return _codex_reasoning_levels(row.get("supported_reasoning_levels"))
    effort = row.get("effort")
    if effort is None:
        return []
    if not isinstance(effort, dict):
        raise ValueError("Invalid reasoning levels")
    levels = effort.get("supported_levels")
    if levels is None:
        return []
    if not isinstance(levels, list) or not levels or len(levels) > 32:
        raise ValueError("Invalid reasoning levels")
    result, seen = [], set()
    for level in levels:
        if not isinstance(level, str) or not level.strip():
            raise ValueError("Invalid reasoning levels")
        level = text_value(level, 64)
        if level in seen:
            continue
        seen.add(level)
        result.append({"effort": level, "description": ""})
    return result


def _codex_reasoning_levels(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("Invalid reasoning levels")
    levels = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("effort"), str):
            raise ValueError("Invalid reasoning levels")
        description = item.get("description", "")
        if (not isinstance(description, str) or len(description) > 1024
                or any(ord(c) < 32 for c in description)):
            raise ValueError("Invalid reasoning levels")
        levels.append({"effort": text_value(item["effort"], 64),
                       "description": description})
    return levels


def _default_reasoning_level(row):
    if "default_reasoning_level" in row:
        value = row.get("default_reasoning_level")
    else:
        effort = row.get("effort")
        value = effort.get("default_level") if isinstance(effort, dict) else None
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Invalid default reasoning level")
    return text_value(value, 64)


def _context_window(row):
    value = row.get("context_window")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 8_192 <= value <= 4_194_304:
        raise ValueError("Invalid context window")
    return value


def _input_modalities(row):
    value = row.get("input_modalities")
    if value is None:
        return ["text"]
    if (not isinstance(value, list) or not value or len(value) > 8
            or any(modality not in {"text", "image", "audio"} for modality in value)):
        raise ValueError("Invalid input modalities")
    return list(dict.fromkeys(value))


def _service_tiers(row):
    value = row.get("service_tiers")
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("Invalid service tiers")
    tiers = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Invalid service tiers")
        tier_id, name = item.get("id"), item.get("name")
        if not isinstance(tier_id, str) or not isinstance(name, str):
            raise ValueError("Invalid service tiers")
        description = item.get("description")
        if description is not None and (not isinstance(description, str)
                                        or len(description) > 1024
                                        or any(ord(c) < 32 for c in description)):
            raise ValueError("Invalid service tiers")
        tier = {"id": text_value(tier_id, 64), "name": text_value(name, 128)}
        if description:
            tier["description"] = description
        tiers.append(tier)
    return tiers


def _template(executable, settings):
    """Clone the pinned runtime's plainest picker-visible catalog entry."""
    result = subprocess.run(
        [executable, "debug", "models", "--bundled"],
        env=probe_environment(settings.environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=BUNDLED_TIMEOUT,
        check=False,
    )
    if result.returncode or len(result.stdout) > BUNDLED_BYTE_LIMIT:
        raise ValueError("Bundled model catalog probe failed")
    rows = strict_json(result.stdout).get("models")
    entries = [row for row in rows or [] if isinstance(row, dict)]
    if not entries:
        raise ValueError("Bundled model catalog is empty")
    preferred = [row for row in entries if row.get("slug") == settings.model]
    if preferred:
        return copy.deepcopy(preferred[0])
    visible = [row for row in entries if row.get("visibility") == "list"]
    # The picker-visible entry the catalog ranks last carries the plainest
    # harness metadata, so no flagship-only behavior leaks to the gateway.
    return copy.deepcopy(max(visible or entries, key=_priority))


def _priority(row):
    value = row.get("priority")
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def _entries(models, template, configured, fallback):
    entries, seen = [], set()
    for spec in models:
        if spec["model"] in seen:
            continue
        seen.add(spec["model"])
        entries.append(_entry(spec, template, len(entries) + 1))
    if configured and configured not in seen:
        spec = _with_reasoning_fallback([_plain_spec(configured)], fallback)[0]
        entries.insert(0, _entry(spec, template, 0))
    return entries


def _plain_spec(model):
    return {"model": model, "display_name": model, "description": None,
            "reasoning_levels": [], "default_reasoning_level": None,
            "input_modalities": ["text"], "service_tiers": [],
            "context_window": None}


def _entry(spec, template, priority):
    entry = copy.deepcopy(template)
    entry.update({
        "slug": spec["model"],
        "display_name": spec["display_name"],
        "description": spec["description"],
        "visibility": "list",
        "supported_in_api": True,
        "priority": priority,
        "supported_reasoning_levels": spec["reasoning_levels"],
        "default_reasoning_level": spec["default_reasoning_level"],
        # Gateway listings do not declare these OpenAI-only request controls;
        # never inherit them from the bundled template.
        "default_reasoning_summary": "none",
        "supports_reasoning_summary_parameter": False,
        "support_verbosity": False,
        "default_verbosity": None,
        "additional_speed_tiers": [],
        "service_tiers": spec["service_tiers"],
        "availability_nux": None,
        "upgrade": None,
        "input_modalities": spec["input_modalities"],
        "supports_image_detail_original": False,
        # The template's search/deferral behavior belongs to the bundled model;
        # a gateway model keeps the conservative unknown-model tool surface.
        "supports_search_tool": False,
        "web_search_tool_type": "text",
        "use_responses_lite": False,
    })
    if spec["context_window"] is not None:
        entry["context_window"] = spec["context_window"]
        entry["max_context_window"] = spec["context_window"]
    for key in ("tool_mode", "multi_agent_version", "multi_agent_reasoning_effort"):
        if key in entry:
            entry[key] = None
    return entry


def _write(path, entries):
    body = json.dumps({"models": entries}, ensure_ascii=False, allow_nan=False).encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(body)
    os.replace(temporary, path)
