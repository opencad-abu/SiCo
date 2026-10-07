"""Compatibility facade for physical PVT adapter owners.

Remove when downstream imports select the planning, launcher and evidence owners.
A READY plan has not executed a simulator or established a design verdict.
"""

from .pvt_launcher import (CommandBuilder, PVTAdapterError, PhysicalPVTLauncher, PhysicalPVTLauncherRegistry, empty_physical_pvt_registry)
from .pvt_plan_models import PVTLaunchContext, PVTLaunchPlan, PVTPointPlan
from .pvt_plan_builder import build_physical_pvt_plan
from .pvt_model_sections import verify_approved_model_sections
from .pvt_result_evaluator import evaluate_physical_pvt_results

__all__ = [
    "CommandBuilder", "PhysicalPVTLauncher", "PhysicalPVTLauncherRegistry",
    "PVTAdapterError", "PVTLaunchContext", "PVTLaunchPlan", "PVTPointPlan",
    "build_physical_pvt_plan", "empty_physical_pvt_registry",
    "evaluate_physical_pvt_results", "verify_approved_model_sections",
]
