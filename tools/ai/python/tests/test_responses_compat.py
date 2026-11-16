"""Provider-specific Responses fields stay in one table and never touch payloads."""

from cadai.responses_compat import codex_options, profile, strip, unsupported_fields


def test_doubao_profile_matches_model_family():
    assert profile("doubao") == "doubao"
    assert profile("Doubao-Pro-32k") == "doubao"
    assert profile("deepseek-v4-flash") is None
    assert unsupported_fields("doubao") == {"reasoning.summary", "text.verbosity"}
    assert unsupported_fields("deepseek-v4-flash") == frozenset()


def test_codex_options_only_suppress_summary():
    assert codex_options("doubao") == {"model_reasoning_summary": "none"}
    assert codex_options("deepseek-v4-flash") == {}


def test_strip_removes_envelope_paths_and_prunes_empty_parents():
    request = {
        "reasoning": {"summary": "auto", "effort": "low"},
        "text": {"verbosity": "low", "format": {"type": "json_schema"}},
        "input": [{"type": "message", "content": "reasoning.summary text.verbosity"}],
    }
    assert strip(request, unsupported_fields("doubao")) == {
        "reasoning": {"effort": "low"},
        "text": {"format": {"type": "json_schema"}},
        "input": [{"type": "message", "content": "reasoning.summary text.verbosity"}],
    }


def test_strip_prunes_a_parent_that_becomes_empty():
    request = {"reasoning": {"summary": "auto"}, "text": {"verbosity": "low"}}
    assert strip(request, unsupported_fields("doubao")) == {}
