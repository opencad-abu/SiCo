"""Compatibility facade for provider and candidate qualification.

Domain owners are split by provider boundary, candidate workflow and report
responsibility. Remove this facade after consumers migrate to those modules.
"""

from .qualification_contract import QUALIFICATION_BLOCKED_INPUT as QUALIFICATION_BLOCKED_INPUT, QUALIFICATION_BLOCKED_PROVIDER as QUALIFICATION_BLOCKED_PROVIDER, QUALIFICATION_CONFIG_VALIDATED as QUALIFICATION_CONFIG_VALIDATED, QUALIFICATION_FAIL as QUALIFICATION_FAIL, QUALIFICATION_FAIL_REVISION as QUALIFICATION_FAIL_REVISION, QUALIFICATION_PASS as QUALIFICATION_PASS, QUALIFICATION_SCHEMA_VERSION as QUALIFICATION_SCHEMA_VERSION
from .qualification_models import CandidateRevision as CandidateRevision, GateFeedback as GateFeedback, ProviderExchange as ProviderExchange, QualificationReport as QualificationReport, RevisionWorkflowResult as RevisionWorkflowResult
from .qualification_provider import qualify_bundle_provider as qualify_bundle_provider, qualify_provider as qualify_provider, qualify_replay_provider as qualify_replay_provider, qualify_with_fallback as qualify_with_fallback
from .qualification_candidate import default_candidate_gate as default_candidate_gate
from .qualification_workflow import run_candidate_revision_workflow as run_candidate_revision_workflow
from .qualification_replay import build_replay_records as build_replay_records
from .qualification_report import write_qualification_report as write_qualification_report

__all__ = ['CandidateRevision', 'GateFeedback', 'ProviderExchange', 'QualificationReport', 'RevisionWorkflowResult', 'QUALIFICATION_BLOCKED_INPUT', 'QUALIFICATION_BLOCKED_PROVIDER', 'QUALIFICATION_CONFIG_VALIDATED', 'QUALIFICATION_FAIL', 'QUALIFICATION_FAIL_REVISION', 'QUALIFICATION_PASS', 'QUALIFICATION_SCHEMA_VERSION', 'build_replay_records', 'default_candidate_gate', 'qualify_bundle_provider', 'qualify_provider', 'qualify_replay_provider', 'qualify_with_fallback', 'run_candidate_revision_workflow', 'write_qualification_report']
from .qualification_config import configuration_validation_report as configuration_validation_report
