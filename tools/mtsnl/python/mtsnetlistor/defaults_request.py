"""Explicit ASI/MAE defaults-probe request identity."""

from __future__ import annotations

from dataclasses import dataclass
from .errors import RequestValidationError
from .model_design import SourceDesign
from .model_validation import SIMULATORS
from .defaults_values import DEFAULTS_PROVIDERS, _safe_text


@dataclass(frozen=True)
class MaeSetup:
    """Explicit Maestro setup/test identity used by the MAE provider.

    A schematic source selection does not identify an ADE test.  Keeping this
    identity separate makes it impossible for the defaults worker to guess a
    test (or accidentally read the active test from an unrelated session).
    """

    library: str
    cell: str
    view: str = "maestro"
    test: str = ""
    history: str | None = None
    application: str | None = None

    def validate(self) -> "MaeSetup":
        # Import lazily to keep this module's existing import graph unchanged.
        from .model import validate_oa_name

        library = validate_oa_name(self.library, "Maestro setup library")
        cell = validate_oa_name(self.cell, "Maestro setup cell")
        view = validate_oa_name(self.view, "Maestro setup view")
        test = _safe_text(self.test, "Maestro test").strip()
        if not test:
            raise RequestValidationError(
                "Maestro test must be set; MAE cannot be inferred from a source cell"
            )
        history = None if self.history is None else _safe_text(self.history, "Maestro history")
        application = (
            None
            if self.application is None
            else _safe_text(self.application, "Maestro application")
        )
        return MaeSetup(library, cell, view, test, history, application)

    def to_dict(self) -> dict[str, str]:
        value = self.validate()
        payload = {
            "library": value.library,
            "cell": value.cell,
            "view": value.view,
            "test": value.test,
        }
        if value.history is not None:
            payload["history"] = value.history
        if value.application is not None:
            payload["application"] = value.application
        return payload


@dataclass(frozen=True)
class DefaultsProbeRequest:
    source: SourceDesign
    dialect: str = "spectre"
    provider: str = "asi_initialization"
    mae_setup: MaeSetup | None = None

    def validate(self) -> "DefaultsProbeRequest":
        source = self.source.validate()
        dialect = str(self.dialect)
        if dialect not in SIMULATORS:
            raise RequestValidationError(f"unsupported simulator dialect: {dialect!r}")
        provider = str(self.provider)
        if provider not in DEFAULTS_PROVIDERS:
            raise RequestValidationError(f"unsupported defaults provider: {provider!r}")
        mae_setup = self.mae_setup
        if provider == "mae_test":
            if mae_setup is None:
                raise RequestValidationError(
                    "MAE provider requires an explicit Maestro setup/test"
                )
            mae_setup = mae_setup.validate()
        elif mae_setup is not None:
            raise RequestValidationError(
                "Maestro setup is only valid with provider='mae_test'"
            )
        return DefaultsProbeRequest(source, dialect, provider, mae_setup)


PdkDefaultsRequest = DefaultsProbeRequest
