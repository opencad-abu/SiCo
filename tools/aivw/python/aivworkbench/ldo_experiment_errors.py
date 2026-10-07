"""Typed LDO experiment boundary failures."""

from __future__ import annotations


class LDOExperimentError(ValueError):
    """Base class for malformed or unsafe LDO experiment requests."""


class LDOExperimentPolicyError(LDOExperimentError):
    """Raised when the recipe-owned experiment policy is malformed."""


class LDOExperimentSafetyError(LDOExperimentError):
    """Raised when a stimulus escapes the declared safe operating domain."""


class LDOExperimentBudgetError(LDOExperimentError):
    """Raised when a plan or policy exceeds a bounded experiment budget."""


class LDOHoldoutLeakError(LDOExperimentError):
    """Raised when hidden holdout identifiers or values enter a public plan."""
