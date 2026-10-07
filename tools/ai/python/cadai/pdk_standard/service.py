"""Workspace standard-data service: query, review and host-confirmed publication."""

import os

from .drafts import Drafts
from .jsonio import fail
from .provenance import verify
from .query import Queries
from .updates import Updates
from .workspace import select


class StandardData:
    def __init__(self, workspace, environment, bridge=None):
        self.workspace, self.environment = workspace, environment
        environment = os.environ if environment is None else environment
        from ..env_names import value

        self.workflow = value(environment, "PDK_WORKFLOW", "design")
        if self.workflow not in {"design", "generation"}:
            fail("SICO_PDK_WORKFLOW must be design or generation")
        self.source_validator = None
        from .completion import Completion

        self.completion = Completion(workspace, environment, bridge)
        from .fact_import import ImportFacts

        self.facts = ImportFacts(workspace, environment, bridge)
        from .fact_collection import CollectFacts

        self.fact_collection = CollectFacts(workspace, environment, bridge) if workspace else None
        from .cdf_validation import Validation
        self.cdf_validation = Validation(workspace, environment, bridge) if workspace else None
        self.queries = Queries()
        self.updates = Updates(workspace, environment) if workspace else None
        self.drafts = Drafts(workspace, environment, bridge) if workspace else None

    def call(self, name, args):
        if not self.workspace:
            fail("Standard PDK tools require an explicit workspace", "workspace_required")
        if name == "get_pdk_data":
            return self.queries.read(
                verify(select(self.workspace, args["library"], self.environment)), args
            )
        if self.workflow != "generation":
            fail(
                "Normal design reads PDK data only. Complete missing facts in a separate PDK "
                "generation workspace/session with SICO_PDK_WORKFLOW=generation.",
                "pdk_generation_required",
            )
        validation_methods = {
            "prepare_pdk_cdf_validation": self.cdf_validation.prepare,
            "advance_pdk_cdf_validation": self.cdf_validation.advance,
            "get_pdk_cdf_validation": self.cdf_validation.store.get,
            "cancel_pdk_cdf_validation": self.cdf_validation.cancel,
            "finalize_pdk_cdf_validation": self.cdf_validation.finish,
        }
        if name in validation_methods:
            return validation_methods[name](args)
        if name == "collect_pdk_data":
            return self.completion.collect(args)
        if name == "collect_pdk_parameter_facts":
            return self.fact_collection.run(args)
        if name == "get_pdk_fact_report":
            return self.fact_collection.reports.get(args)
        if name == "import_pdk_facts":
            return self.facts.run(args)
        if name == "publish_pdk_data":
            from .publication import publish

            return publish(
                self.workspace,
                args["library"],
                args["revision"],
                self.environment,
                source_validator=self.source_validator,
            )
        if name == "prepare_pdk_data_update":
            return self.updates.prepare(args)
        if name == "apply_pdk_data_update":
            return self.updates.apply(args)
        if name == "prepare_pdk_parameter_draft":
            return self.drafts.prepare(args)
        if name == "get_pdk_parameter_draft":
            return self.drafts.get(args)
        if name == "submit_pdk_parameter_batch":
            return self.drafts.submit(args, self.updates)
        if name == "cancel_pdk_parameter_draft":
            return self.drafts.cancel(args)
        fail("Unknown standard PDK operation")
