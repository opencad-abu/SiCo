"""Generic public trajectory model-class generation gate."""

import json

from ..executor import ExecutorResult
from ..trajectory_evidence import public_trajectories
from ..workspace import sha256_file, stable_digest, write_json_once


def run_trajectory_model(context):
    try:
        plan, evidence, waves = public_trajectories(context)
        options_path = context.recipe.input_path("identification_options")
        options = json.loads(options_path.read_text())
        plugin = context.metadata["registry"].require_model_class(context.recipe.model_class)
        if plugin.handler is None or "system-identification" not in plugin.capabilities:
            raise ValueError("model class does not support trajectory identification")
        model = plugin.handler((plan, evidence, waves, options))
        # Rendering is model-class owned too; no target equations in this gate.
        if model["model_class"] != plugin.name or not hasattr(plugin.handler, "render_source"):
            raise ValueError("model renderer is unavailable")
        source = context.gate_root/"candidate.sv"
        with source.open("x") as stream:
            stream.write(plugin.handler.render_source(model))
        path = context.gate_root/"model.json"
        write_json_once(path, model)
        provenance = context.gate_root/"generation-evidence.json"
        write_json_once(provenance, {"model_class": plugin.name, "plugin_version": plugin.version,
            "source_generation": evidence["source_generation"], "experiment_digest": stable_digest(plan),
            "options_sha256": sha256_file(options_path), "candidate_sha256": sha256_file(source),
            "model_sha256": sha256_file(path), "generation_mode": model["generation_mode"],
            "ai_generation": "NOT_PERFORMED", "holdout_used": False, "qualification": "NOT_ESTABLISHED"})
        return ExecutorResult("PASS", {"scope": "public_identification_candidate", "qualification": "NOT_ESTABLISHED"},
            {"dynamic_model": str(path.relative_to(context.run.payload_root)), "dynamic_model_sha256": sha256_file(path),
             "model_source": str(source.relative_to(context.run.payload_root)), "model_source_sha256": sha256_file(source),
             "source_generation": evidence["source_generation"], "experiment_digest": stable_digest(plan)}, (source, path, provenance))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return ExecutorResult("BLOCKED_INPUT", {"code": "identification_failed", "reason": str(exc)})
