"""Canonical standard-data tool contract, shared by MCP and the SiCo host."""

from ..pdk_errors import PdkArgumentError
from .draft_contract import CHANGES as _UPDATE_CHANGES
from .draft_contract import ENTRY as _MAPPING_ENTRY
from .draft_contract import PAGE as _DRAFT_PAGE
from .draft_contract import REF as _DRAFT_REF
from .draft_contract import SOURCE as _MANUAL_SOURCE
from .fact_contract import COLLECT as _FACT_COLLECT, QUERY as _FACT_QUERY
from . import cdf_validation_contract as _CDF_VALIDATION
from .jsonio import LIMIT, encode
from .review import MAX_REVIEW_BYTES

TEXT = {"type": "string", "minLength": 1, "maxLength": 256}
COMMON = {"library": TEXT}
PAGE = {"page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        "cursor": TEXT}


def tool(name, description, properties, required, read=True, help_text=None):
    """One definition; ``help_text`` is on-demand detail and stays off the wire."""
    row = {"name": name, "description": description,
           "inputSchema": {"type": "object", "properties": properties, "required": required,
                           "additionalProperties": False},
           "annotations": {"readOnlyHint": read, "destructiveHint": False,
                           "idempotentHint": read, "openWorldHint": False}}
    if help_text:
        row["help"] = help_text
    return row


TOOLS = [
    tool("prepare_pdk_cdf_validation",
         "Generation-only: retain up to 16 source-bound diagnostic cases for one observed device. "
         "order lists all confirmed core inputs; each phase supplies active required inputs only. "
         "Unknown domains permit investigation, never imply legal values. Cases declare purpose, reason and "
         "observed inputs/derived outputs; optional transition changes mode on the SAME instance. "
         "No callbacks or OA writes during preparation. Reuse request_id only for identical contents.",
         _CDF_VALIDATION.PREPARE, list(_CDF_VALIDATION.PREPARE), read=False),
    tool("advance_pdk_cdf_validation",
         "Generation-only: execute ONE pending case using the installed built-in CDF executor in a unique "
         "background SicoTest schematic in this workspace. Check current revision and saved sources; "
         "save requested/actual values, callback checkpoints, warnings and saved readback. "
         "Never open windows or overwrite views. Interrupted dispatch is uncertain and is NEVER replayed; "
         "inspect retained evidence before creating a new suite. Does not infer ranges or grant device use.",
         _CDF_VALIDATION.ADVANCE, ["validation_ref"], read=False),
    tool("get_pdk_cdf_validation",
         "Generation-only: page durable CDF cases/results or list suites by library after restart. "
         "Supply validation_ref OR library. Accepted/adjusted/rejected/unsupported/uncertain are observed "
         "case outcomes, not whole-domain qualification. No source recapture or callback execution.",
         _CDF_VALIDATION.QUERY, []),
    tool("cancel_pdk_cdf_validation",
         "Generation-only: stop before the next case; retain all targets and evidence. Does not interrupt "
         "an in-flight native callback or delete partial views. Remaining cases are cancelled.",
         _CDF_VALIDATION.ADVANCE, ["validation_ref"], read=False),
    tool("finalize_pdk_cdf_validation",
         "Generation-only: evaluate case/mode coverage. Optional apply_execution updates ONLY execution "
         "after baseline, varied inputs, boundary/off-grid, all modes and transitions pass coverage, "
         "sources remain valid and input domains are already known. Unknown constraints or unsupported "
         "callbacks block publication. No range inference, permission changes, or design-ready claim.",
         _CDF_VALIDATION.FINISH, ["validation_ref"], read=False),
    tool("collect_pdk_parameter_facts",
         "Generation-only: collect bounded source facts for confirmed core CDF inputs and necessary dependencies. "
         "Source must reference the registered manual/file with revision and locator. Established definitions "
         "or complete input constraints may update facts; partial bounds, PCell limits and "
         "recommendations stay context, never legal domains. Explicit input_unit scales numeric facts into "
         "the parameter unit. Preserve known conflicts unless exact expected and source review are supplied; "
         "no permission, execution or interface-scope changes. Unresolved records retain reasons; an "
         "incomplete domain may be recorded as value={kind:unknown,reason:<same reason>} without input_unit. "
         "Fresh OA/document checks precede the atomic overlay commit; request_id retries are idempotent.",
         _FACT_COLLECT, ["library", "revision", "request_id", "facts"], read=False,
         help_text=(
             "Demotion of an unsupported known domain with exact expected is allowed; this does not "
             "close the generation gap.")),
    tool("get_pdk_fact_report", "Generation-only: page a retained fact collection report, source evidence, "
         "unit normalization and unresolved/conflict details. Reports are historical process evidence, not "
         "a second effective domain. Supply report_ref for details or library to list reports after restart. "
         "Query effective facts with get_pdk_data.",
         _FACT_QUERY, []),
    tool("get_pas_dpt", "PDK generation/maintenance only: read one cell's PAS device property table "
         "(min/max style fields such as minL, maxL, minFw, maxFw) when the cell carries the pdk.dat "
         "marker of a PAS/GTE-built PDK. Other PDKs report pdk_dat=false with no fields; the table "
         "field names are PDK-defined and the reply stays bounded. Read-only: no callbacks, no writes. "
         "Treat returned ranges as input-constraint evidence for review, never as automatic permission.",
         {"library": TEXT, "cell": TEXT}, ["library", "cell"]),
    tool("get_pdk_source_impact",
         "PDK generation/maintenance only: map source changes through the declared dependency graph. "
         "Re-hash file/model bytes at bound roots; map CDF/symbol/interface changes to affected "
         "documents, devices and user confirmations. Unaffected objects retain validity and are "
         "counted only. Optional device requires a session bridge for a live fingerprint re-check. "
         "Read-only: no collection, OA writes or index changes.",
         {**COMMON, "device": TEXT}, ["library"]),
    tool('import_pdk_facts', 'PDK generation/maintenance only: reuse objective facts from a reviewed standard package/index. '
         'Requires exact destination and source revisions. Validates bound source bytes and live OA '
         'dependencies, then merges atomically into the cumulative overlay. Never imports use/write/requirement '
         'decisions or callback execution claims. Known conflicting facts fail by default; preserve_conflicts=true '
         'keeps existing field values and records disagreements as unresolved. Repeated identical imports are '
         'no-ops.',
         {**COMMON, 'revision': TEXT, 'source_index': {'type': 'string', 'minLength': 1, 'maxLength': 4096},
          'source_revision': TEXT, 'preserve_conflicts': {'type': 'boolean', 'default': False}},
         ['library', 'revision', 'source_index', 'source_revision'], read=False,
         help_text=(
             'An explicit legacy standard checkpoint is accepted; denied and undecided devices are '
             'included. This imports sourced facts; it does not interpret arbitrary PDFs or qualify '
             'design behavior.')),
    tool("collect_pdk_data", "PDK generation/maintenance only: collect one device's saved symbol "
         "body/anchor facts; when model.json is absent, derive it from declared deck sections and "
         "fingerprint model bytes. Checks the exact revision/sources, keeps raw evidence, publishes "
         "a cumulative overlay without changing user rules. No callbacks/OA writes. Device-specific "
         "interface gaps return a specific note; unresolved grid needs source/user completion. "
         "Repeat calls reuse the attempt; refresh=true reads again.",
         {**COMMON, "device": TEXT, "revision": TEXT, "refresh": {"type": "boolean", "default": False},
          "netlist_name": TEXT, "identity_evidence": TEXT, "builtin_evidence": TEXT},
         ["library", "device", "revision"], read=False,
         help_text=(
             "netlist_name/identity_evidence together override the CDF model default for one "
             "user-ruled vendor anomaly (manual-documented identity whose CDF default has no deck); "
             "the ruling is recorded as a user source with the retained scope record, and the CDF "
             "field itself is never rewritten. Without the pair the CDF default stays authoritative. "
             "builtin_evidence names a recorded simulator-builtin netlist probe (private evidence): "
             "when the package ships no model decks, the probe's netlist lines are cross-checked "
             "against the captured simInfo and the confirmed inputs, and only confirmed inputs plus "
             "fixed non-empty string tokens are transmitted. For external-model devices the same "
             "probe also supplies the recorded setup (switch/stop views and options) when no setup "
             "has been collected yet. The probe itself stays private "
             "(reference only, never copied into the package).")),

    tool("get_pdk_data", "Read SICO-PDK-DATA 1.1/1.0 from the effective package; no implicit "
         "collection, probe or update. Default search lists published circuit/simulation devices. "
         "section=missing reports gaps without authorizing an update; include_unconfirmed is "
         "generation-only. Revision cursors; responses ≤16 KiB.",
         {**COMMON, **PAGE, "device": TEXT, "parameter": TEXT, "category": TEXT,
          "use": {"type": "string", "enum": ["circuit", "testbench", "extraction", "verification"], "default": "circuit"},
          "include_unconfirmed": {"type": "boolean", "default": False},
          "section": {"type": "string", "enum": ["devices", "missing", "collection", "blockers", "package", "cdf", "cdf_modes", "symbol", "simulation",
                                                       "category", "file", "model", "sources", "properties", "iv"],
                      "default": "devices"}}, ["library"],
         help_text=(
             "blockers is the bounded root-cause report with one recommended next_action per cause; "
             "section=model returns configs, corners and the derived model/subckt card inventory "
             "(name/kind/file_ref/lines/configs/sections/element_type/ports); "
             "missing lists gaps; cdf_modes shows selectors. Map manual CDF terms to observed "
             "names and confirm editable inputs; discovery is not write permission. Qualification "
             "is per published revision: one acceptance covers all circuit/testbench-allowed "
             "devices at its content/source digest; normal sessions check only effective "
             "revision. pdk_acceptance_required = absent. Legacy caches: inspection-only.")),
    tool("resolve_pdk_baseline",
         "Resolve a disagreement between the workspace .sico/ai/pdk-data baseline and "
         "SICO_PDK_DATA after the user explicitly chooses. sync_shared copies the shared "
         "baseline into the workspace and archives the local package; keep_workspace keeps "
         "the local baseline after standard validation. Requires baseline_ref from "
         "get_pdk_preparation and choice_confirmed=true. Does not collect or publish PDK data.",
         {**COMMON, "baseline_ref": TEXT,
          "decision": {"type": "string", "enum": ["sync_shared", "keep_workspace"]},
          "choice_confirmed": {"type": "boolean", "default": False}},
         ["library", "baseline_ref", "decision"], read=False),
    tool("publish_pdk_data", "PDK generation only: validate the exact revision for creation, "
         "parameters, wiring, netlist and simulation; atomically publish a reusable package. "
         "Missing facts/acceptance fail without changing the index. Existing confirmations are "
         "reused; IV/all-corner scans optional. Requires PDK maintenance mode. Failures return "
         "prioritized root causes; use get_pdk_data(section=blockers).",
         {**COMMON, "revision": TEXT, "acceptance_evidence": TEXT,
          "unfinished_options": {"type": "array", "items": TEXT, "maxItems": 16}},
         ["library", "revision", "acceptance_evidence", "unfinished_options"], read=False,
         help_text=(
             "acceptance_evidence is a v2 evidence file recording pre-run subject "
             "(projection, package_id, effective revision, content_digest), source fingerprints, "
             "private run references and per-device results for creation, parameter_roundtrip, "
             "wiring, netlist and simulation_config. The verifier rechecks content and sources "
             "after execution. Publication compares and preserves the recorded identity; "
             "missing, failed, stale or incomplete evidence is rejected. Legacy v1 needs fresh "
             "acceptance; never fill or rebind historical evidence. unfinished_options lists "
             "optional work not performed (e.g. iv, all_corner_scans; empty if none). Scope "
             "records libraries, device count, simulators and corners. Data publication grants "
             "no runtime-software distribution authorization.")),
    tool("prepare_pdk_data_update", "Generation/maintenance only (PDK data maintenance mode): preview exact "
         "changes to device usage or collected CDF/symbol rules against the effective standard revision. "
         "No OA writes, no publication. Use observed CDF keys, not aliases or another PDK's "
         "whitelist; reuse source-valid confirmations. Submit small add/replace/remove "
         "object-member patches (arrays replaced whole) for needed records only. The host must obtain "
         "a real user answer covering the returned changes; a model assertion is not confirmation.",
         {**COMMON, "revision": TEXT, "changes": _UPDATE_CHANGES},
         ["library", "revision", "changes"], read=False,
         help_text=(
             "For new core CDF policies, first consult the current PDK user manual by device type and "
             "discuss its mapping to actual CDF names/form labels and the user's desired inputs; retain "
             "manual locations and unresolved differences. The complete UTF-8 formatted before/after "
             f"review is retained up to {MAX_REVIEW_BYTES} bytes (not the patch input size); the host "
             "paginates the full review in the GUI and the approval question is only a summary. "
             "Only the retained-evidence storage limit can reject a preview; GUI paging does not "
             "create additional approvals.")),
    tool("apply_pdk_data_update", "PDK generation/maintenance workflow only: apply a retained standard-data update only after host-verified user "
         "confirmation. Rechecks the effective revision, dependencies and review evidence, then atomically "
         "publishes a cumulative workspace overlay. No confirmation boolean or user identity is accepted "
         "from the agent. Without a host answer, return the same pending review.",
         {"update_ref": TEXT}, ["update_ref"], read=False),
    tool("prepare_pdk_baseline_update",
         "Generation/maintenance only (PDK data maintenance mode): preview a reviewed baseline "
         "maintenance update: verified identity facts (real pdk_version and process options; directory "
         "names are not evidence) and/or live source refreshes (observed devices, byte resources). "
         "The review shows exact before/after fields, the re-verification scope of affected "
         "documents/devices/confirmations and the delta changes re-approved under the answer. "
         "Nothing is written before the host-verified user answer.",
         {**COMMON, "revision": TEXT, "pdk_version": TEXT,
          "options": {"type": "object", "minProperties": 1, "maxProperties": 16,
                      "propertyNames": {"type": "string", "minLength": 1, "maxLength": 128},
                      "additionalProperties": {"type": ["string", "number", "boolean"]}},
          "refresh_devices": {"type": "array", "maxItems": 8, "items": TEXT},
          "refresh_resources": {"type": "array", "maxItems": 32, "items": TEXT},
          "reason": TEXT},
         ["library", "revision"], read=False,
         help_text=(
             "Only verified identity facts and live-re-verified source refreshes belong here; unknown "
             "values stay unknown, and a refresh records only fingerprints read from the live source "
             "(bytes are re-hashed, devices re-captured read-only). Refreshing never silently renews "
             "affected confirmations: the re-verification scope is listed for follow-up re-collection "
             "and re-review. With an existing cumulative overlay the apply records one explicit rebase; "
             "a cancelled or failed attempt keeps the current revision.")),
    tool("apply_pdk_baseline_update",
         "Generation/maintenance workflow only: apply a retained baseline identity update only after "
         "host-verified user confirmation. Builds the new complete baseline, records the explicit "
         "rebase (or refuses on any precondition conflict), proves all logical documents unchanged, "
         "and switches the baseline and overlay index entries atomically. Cancelled or failed "
         "attempts keep the current revision.",
         {"update_ref": TEXT}, ["update_ref"], read=False),
    tool("prepare_pdk_parameter_draft",
         "Generation-only: create or revise a durable manual/CDF mapping draft. Source uses a registered "
         "document file_ref with verified byte dependency and declared document version; without a manual "
         "record unavailable_reason and unresolved candidates. Reads saved OA metadata to validate actual "
         "CDF keys and capture form labels, without callbacks or writes. Parameter candidates require meaning "
         "and user_term. Entries use caller-stable IDs for "
         "exceptions and grouping; role distinguishes input, selector, derived or device usage. Existing "
         "drafts require draft_version; upsert explicitly revises unsubmitted entries. Drafts grant no permission.",
         {**COMMON, "revision": TEXT, "source": _MANUAL_SOURCE,
          "draft_ref": _DRAFT_REF, "draft_version": TEXT,
          "mode": {"type": "string", "enum": ["append", "upsert"], "default": "append"},
          "entries": {"type": "array", "minItems": 1, "maxItems": 32, "items": _MAPPING_ENTRY}},
         ["library", "revision", "source", "entries"], read=False),
    tool("get_pdk_parameter_draft",
         "Generation-only: list durable drafts (optional library filter), or read one draft's entries/batches "
         "with revision-bound pagination. Returns computed source/stale state without mutations. "
         "Applied receipts remain queryable after restart; candidate mappings never grant permission.",
         {"draft_ref": _DRAFT_REF, **COMMON, **_DRAFT_PAGE,
          "section": {"type": "string", "enum": ["entries", "batches"], "default": "entries"}}, []),
    tool("submit_pdk_parameter_batch",
         "Generation-only: submit selected mapped entries and exact device-use/CDF-policy patches to the "
         "existing host-confirmed update workflow. Requires current effective revision and draft_version. "
         "Manual identity, locators, meanings, observed labels and user terms are included in the same "
         "bounded review digest. Source-valid identical user-confirmed policies are reused without asking. "
         "Document facts must use fact ingestion, not this policy batch. Apply a returned update_ref only "
         "after the host answers the exact review. After each batch read current revisions before continuing.",
         {"draft_ref": _DRAFT_REF, "draft_version": TEXT, "revision": TEXT, "batch_id": TEXT,
          "entry_ids": {"type": "array", "minItems": 1, "maxItems": 32, "items": TEXT, "uniqueItems": True},
          "changes": _UPDATE_CHANGES},
         ["draft_ref", "draft_version", "revision", "batch_id", "entry_ids", "changes"], read=False),
    tool("cancel_pdk_parameter_draft",
         "Generation-only: cancel a durable mapping draft at its exact draft_version. Retains all evidence "
         "and previously committed policies; uncommitted reviews from this draft can no longer apply.",
         {"draft_ref": _DRAFT_REF, "draft_version": TEXT}, ["draft_ref", "draft_version"], read=False),

]
NAMES = frozenset(t["name"] for t in TOOLS)


def arguments(name, args):
    from ..circuit_spec_schema import CircuitSpecError, validate

    schema = next(t["inputSchema"] for t in TOOLS if t["name"] == name)
    try:
        if len(encode(args)) > LIMIT:
            raise PdkArgumentError("Standard tool arguments exceed 16 KiB")
        validate(args, schema)
    except (CircuitSpecError, TypeError, ValueError) as exc:
        raise PdkArgumentError(str(exc)) from exc
    result = {**{k: v["default"] for k, v in schema["properties"].items() if "default" in v}, **args}
    if name == "get_pdk_data":
        if result["section"] in {"cdf", "cdf_modes", "symbol", "simulation", "properties", "iv"} and not result.get("device"):
            raise PdkArgumentError("Per-device section requires device ID")
        if result.get("parameter") and result["section"] != "cdf":
            raise PdkArgumentError("parameter filter requires cdf section")
    if name == "prepare_pdk_parameter_draft" and bool(result.get("draft_ref")) != bool(result.get("draft_version")):
        raise PdkArgumentError("draft_ref and draft_version must be supplied together")
    return result
