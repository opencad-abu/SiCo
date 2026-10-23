"""Workspace standard-data service: query, review and host-confirmed publication."""

import os

from .drafts import Drafts
from .baseline import Baseline
from .jsonio import fail
from .provenance import verify
from .query import Queries
from .updates import Updates
from .workspace import select


class StandardData:
    def __init__(self, workspace, environment, bridge=None):
        self.workspace, self.environment = workspace, environment
        self.bridge = bridge
        environment = os.environ if environment is None else environment
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
        self.baseline = Baseline(workspace, environment, bridge) if workspace else None
        self.drafts = Drafts(workspace, environment, bridge) if workspace else None

    def call(self, name, args):
        if not self.workspace:
            fail("Standard PDK tools require an explicit workspace", "workspace_required")
        if name == "get_pdk_data":
            return self.queries.read(
                verify(select(self.workspace, args["library"], self.environment)), args
            )
        if name == "resolve_pdk_baseline":
            from .baseline_choice import resolve

            return resolve(self.workspace, args["library"], self.environment, args)
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
        if name == "get_pas_dpt":
            from .pas_dpt import read

            if self.bridge is None:
                fail("PAS DPT reads require the live session bridge", "bridge_unavailable")
            return read(self.bridge, args["library"], args["cell"])
        if name == "get_pdk_source_impact":
            from .impact import review

            return review(self.workspace, self.environment, self.bridge, args)
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
                acceptance=args["acceptance_evidence"],
                unfinished=args["unfinished_options"],
            )
        if name == "prepare_pdk_data_update":
            return self.updates.prepare(args)
        if name == "apply_pdk_data_update":
            return self.updates.apply(args)
        if name == "prepare_pdk_baseline_update":
            return self.baseline.prepare(args)
        if name == "apply_pdk_baseline_update":
            return self.baseline.apply(args)
        if name == "prepare_pdk_parameter_draft":
            return self.drafts.prepare(args)
        if name == "get_pdk_parameter_draft":
            return self.drafts.get(args)
        if name == "submit_pdk_parameter_batch":
            return self.drafts.submit(args, self.updates)
        if name == "cancel_pdk_parameter_draft":
            return self.drafts.cancel(args)
        fail("Unknown standard PDK operation")
