"""Configuration owners preserve validation, aliases and service materialization."""

from dataclasses import asdict, replace
from pathlib import Path

import pytest

from sico.configuration.background import BackgroundSettings
from sico.configuration.codex import codex_idle_timeout
from sico.configuration.provider import validate_config
from sico.service.background_config import runtime_settings
from sico.service.background_worker import BackgroundRuntime, Unavailable
from sico.transport.framing import ProtocolError


def test_existing_public_exports_refer_to_one_owner():
    from sico.configuration import codex, lsf, provider
    from sico.codex import settings
    from sico.providers import config
    from sico.service import lsf_config

    assert config.validate_config is provider.validate_config
    for name in ("DEFAULT_IDLE_TIMEOUT", "codex_idle_timeout", "validate_codex"):
        assert getattr(settings, name) is getattr(codex, name)
    for name in ("LsfConfig", "label", "lsf_settings"):
        assert getattr(lsf_config, name) is getattr(lsf, name)


def test_background_runtime_inherits_one_field_model_and_delays_availability_check():
    row = dict(virtuoso="/missing/virtuoso", context="/missing/context.cxt",
               context_sha256="0" * 64, version="fixture", libraries={"Work": "/missing/Work"})
    values = BackgroundSettings.from_record(row)
    runtime = runtime_settings(row)
    assert type(runtime) is BackgroundRuntime
    assert isinstance(runtime, BackgroundSettings)
    assert asdict(runtime) == asdict(values)
    assert runtime.virtuoso == Path(row["virtuoso"])
    assert runtime.xvfb == Path("/usr/bin/Xvfb")
    row["libraries"]["Other"] = "/missing/Other"
    assert "Other" not in runtime.libraries
    assert replace(runtime) == runtime
    with pytest.raises(Unavailable, match="virtuoso_unavailable"):
        runtime.validate()


@pytest.mark.parametrize("row", [None, [], {}, {"virtuoso": "/missing"},
    dict(virtuoso=None, context="/x", context_sha256="", version="", libraries={}),
    dict(virtuoso="/x", context="/x", context_sha256="", version="", libraries=None)])
def test_background_record_rejections_match_service(row):
    with pytest.raises(ProtocolError) as values:
        BackgroundSettings.from_record(row)
    with pytest.raises(ProtocolError) as runtime:
        runtime_settings(row)
    assert str(runtime.value) == str(values.value)


@pytest.mark.parametrize("protocol", ["anthropic", "openai", "responses"])
def test_provider_validation_preserves_return_and_normalization_contract(protocol):
    row = dict(provider=protocol, base_url="https://example.test/v1", model="fixture")
    selected = validate_config(row)
    if protocol == "responses":
        assert selected == {**row, "backend": "codex"}
        assert selected is not row and "backend" not in row
    else:
        assert selected is row


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf"), "bad", 86401])
def test_idle_timeout_rejects_invalid_configured_budgets(value):
    with pytest.raises(ValueError, match="Invalid Codex model idle timeout"):
        codex_idle_timeout({}, value)


def test_idle_timeout_retains_environment_priority_and_rounding():
    assert codex_idle_timeout({}, 120.5) == 121
    assert codex_idle_timeout({"SICO_MODEL_IDLE_TIMEOUT": "30.2"}, 120) == 31
