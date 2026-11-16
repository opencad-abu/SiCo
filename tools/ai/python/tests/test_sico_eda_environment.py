"""New EDA provenance and LSF metadata survive sanitation without stale aliases."""

import pytest

from cadenv import VIRTUOSO_ENV_NAMES, preserve_virtuoso_environment
from cadai import terminal_environment
from sico.service.background_environment import capture_environment
from sico.codex.runtime import mcp_environment_vars


def test_provenance_new_empty_is_authoritative_and_capture_is_idempotent():
    env = {"PATH": "/node/bin", "SICO_VIRTUOSO_PATH": "",
           "CAD_VIRTUOSO_PATH": "/old/bin", "SICO_VIRTUOSO_MODULEPATH": "/site/modules"}
    preserve_virtuoso_environment(env)
    assert env["SICO_VIRTUOSO_PATH"] == ""
    assert env["SICO_VIRTUOSO_MODULEPATH"] == "/site/modules"
    assert not any(key.startswith("CAD_VIRTUOSO_") for key in env)
    original = dict(env)
    preserve_virtuoso_environment(env)
    assert env == original
    assert capture_environment(env)["PATH"] == ""


@pytest.mark.parametrize("prefix", ["SICO_VIRTUOSO_", "CAD_VIRTUOSO_"])
def test_registered_provenance_is_forwarded_but_credential_aliases_are_excluded(prefix):
    env = {prefix + name: "fixture" for name in VIRTUOSO_ENV_NAMES}
    env[prefix + "SECRET"] = "never-forward"
    assert prefix + "SECRET" not in mcp_environment_vars(env)
    assert set(prefix + name for name in VIRTUOSO_ENV_NAMES) <= set(mcp_environment_vars(env))
    if prefix == "CAD_VIRTUOSO_":
        with pytest.raises(ValueError, match="removed"):
            capture_environment(env, credential=prefix + "MODULEPATH")
    else:
        assert "MODULEPATH" not in capture_environment(env, credential=prefix + "MODULEPATH")


@pytest.mark.parametrize("configured", ["", "en_US.UTF-8"])
def test_new_locale_does_not_fall_back_to_invalid_legacy(configured):
    env = {"SICO_AI_UTF8_LOCALE": configured, "CAD_AI_UTF8_LOCALE": "invalid",
           "LC_ALL": "C.UTF-8"}
    assert terminal_environment._select_utf8_locale(env, lambda _: True) == (configured or "C.UTF-8")


def test_new_invalid_locale_is_rejected_even_with_valid_legacy():
    with pytest.raises(ValueError, match="SICO_AI_UTF8_LOCALE"):
        terminal_environment._select_utf8_locale({"SICO_AI_UTF8_LOCALE": "C",
            "CAD_AI_UTF8_LOCALE": "C.UTF-8"}, lambda _: True)


def test_lsf_retired_metadata_rejects_and_current_empty_wins(monkeypatch):
    monkeypatch.setattr(terminal_environment, "_locale_supports_utf8", lambda *_: True)
    env = {"CAD_AI_LSF_HANDOFF": "1", "CAD_AI_LSF_SUBMIT_DISPLAY": ":1", "DISPLAY": ":1"}
    with pytest.raises(ValueError, match="removed"):
        terminal_environment.prepare_input_environment(env)
    env = {"SICO_AI_LSF_HANDOFF": "", "CAD_AI_LSF_HANDOFF": "1"}
    assert terminal_environment.prepare_input_environment(env) == ()
    assert not any("LSF_" in key for key in env)
