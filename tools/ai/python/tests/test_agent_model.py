"""Project model settings default to Codex/Responses and validate backend pairs."""

import pytest
from sico.agent_resources import model


def test_default_model_settings_and_python_round_trip(tmp_path):
    initial = model.read(tmp_path)
    assert (initial["backend"], initial["provider"]) == ("codex", "responses")
    assert initial["revision"] is None

    saved = model.save(tmp_path, "python", "openai", expected_revision=None)
    assert (saved["backend"], saved["provider"]) == ("python", "openai")
    assert model.read(tmp_path)["provider"] == "openai"

    with pytest.raises(model.ModelConflictError):
        model.save(tmp_path, "codex", "responses", expected_revision=None)


def test_invalid_backend_provider_pairs_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="协议"):
        model.save(tmp_path, "codex", "anthropic", expected_revision=None)
    with pytest.raises(ValueError, match="后端"):
        model.save(tmp_path, "unknown", "responses", expected_revision=None)
