"""Product-specific exceptions."""


class AivwError(Exception):
    """Base error shown by the ``aivw`` command."""


class ProfileError(AivwError):
    """A checked-in project or toolchain profile is invalid."""


class EnvironmentError(AivwError):
    """The isolated EDA environment cannot be established."""


class WorkspaceError(AivwError):
    """The current working directory cannot host run artifacts."""


class RecipeError(AivwError):
    """A workflow recipe is invalid or references an unavailable plugin."""


class ExecutorError(AivwError):
    """A registered workflow executor violated its contract or could not run."""
