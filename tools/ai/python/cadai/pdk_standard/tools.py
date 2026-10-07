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


def tool(name, description, properties, required, read=True):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required,
                            "additionalProperties": False},
            "annotations": {"readOnlyHint": read, "destructiveHint": False,
                            "idempotentHint": read, "openWorldHint": False}}


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
         "Source must reference the current registered manual/file with revision and locator. Established definitions "
         "or complete input constraints may update facts; partial bounds, PCell limits, model characterization and "
         "recommendations are retained as context, never promoted to legal domains. Explicit input_unit scales "
         "numeric facts into the parameter unit. Preserve known conflicts unless exact expected and source review "
         "are supplied; no permission, execution or interface-scope changes. Unresolved records retain reasons; "
         "an incomplete domain may be recorded as value={kind:unknown,reason:<same reason>} without input_unit, "
         "including demotion of an unsupported known domain with exact expected. This does not close the gap. "
         "Fresh OA/document checks precede atomic overlay commit; durable request_id retries are idempotent.",
         _FACT_COLLECT, ["library", "revision", "request_id", "facts"], read=False),
    tool("get_pdk_fact_report", "Generation-only: page a retained fact collection report, source evidence, "
         "unit normalization and unresolved/conflict details. Reports are historical process evidence, not "
         "a second effective domain. Supply report_ref for details or library to list reports after restart. "
         "Query effective facts with get_pdk_data.",
         _FACT_QUERY, []),
    tool('import_pdk_facts', 'PDK generation/maintenance only: reuse objective facts from a reviewed standard package/index, '
         'including an explicit legacy standard checkpoint. Requires exact destination and source revisions. '
         'Validates original document/model bytes at current bound roots and live OA dependencies, then atomically '
         'merges into the cumulative overlay. Includes denied and undecided devices; never imports use/write/requirement '
         'decisions or callback execution claims. Known conflicting facts fail by default; preserve_conflicts=true '
         'keeps existing field values and records disagreements without asserting they are resolved. '
         'Repeated identical imports are no-ops. '
         'This imports sourced facts; it does not interpret arbitrary PDFs or qualify design behavior.',
         {**COMMON, 'revision': TEXT, 'source_index': {'type': 'string', 'minLength': 1, 'maxLength': 4096},
          'source_revision': TEXT, 'preserve_conflicts': {'type': 'boolean', 'default': False}},
         ['library', 'revision', 'source_index', 'source_revision'], read=False),
    tool("collect_pdk_data", "PDK generation/maintenance only (host SICO_PDK_WORKFLOW=generation): explicitly collect missing saved symbol body/anchor facts for one observed device. "
         "Checks the exact effective revision and source fingerprints, keeps raw evidence, and publishes a "
         "cumulative workspace overlay without changing user rules. Does not execute callbacks or write OA. "
         "Unresolved geometry/grid still requires source or interactive completion. Repeated calls reuse the "
         "recorded attempt; refresh=true explicitly reads again, but cannot rebase changed dependencies.",
         {**COMMON, "device": TEXT, "revision": TEXT, "refresh": {"type": "boolean", "default": False}},
         ["library", "device", "revision"], read=False),

    tool("get_pdk_data", "Read SICO-PDK-DATA 1.1.0 (legacy 1.0.0 inspection supported) from the effective workspace package. "
         "No implicit collection, probe or update. Default device search only lists devices from "
         "the published circuit/simulation profile; section=missing reports generation gaps and "
         "never authorizes an update. Use include_unconfirmed=true only for the PDK generation workflow. "
         "section=collection lists objective gaps across all devices regardless of cell-use policy, with detailed CDF facts "
         "only for confirmed editable inputs and minimal required dependency facts. Unrelated auxiliary parameters "
         "are retained but excluded from the enrichment backlog; unknown input scope is reported once, never expanded to all CDF properties. "
         "For an unconfirmed core CDF scope, consult this PDK version's user manual by device type, map manual terms, "
         "form labels and user terminology to observed CDF names, then discuss the intended editable inputs with the user. "
         "Never reuse another PDK's parameter-name whitelist. "
         "section=missing remains a design qualification query. section=cdf_modes shows finite selector input truth tables. "
         "CDF discovery is not permission to write. Follow revision-bound cursors; every response is at most 16 KiB.",
         {**COMMON, **PAGE, "device": TEXT, "parameter": TEXT, "category": TEXT,
          "use": {"type": "string", "enum": ["circuit", "testbench", "extraction", "verification"], "default": "circuit"},
          "include_unconfirmed": {"type": "boolean", "default": False},
          "section": {"type": "string", "enum": ["devices", "missing", "collection", "package", "cdf", "cdf_modes", "symbol", "simulation",
                                                       "category", "file", "model", "sources", "properties", "iv"],
                      "default": "devices"}}, ["library"]),
    tool("publish_pdk_data", "PDK generation workflow only: validate the exact effective revision for "
         "schematic creation, parameters, wiring, netlisting and simulation configuration; "
         "atomically publish a reusable package. Missing facts fail without changing the index. "
         "Existing rule confirmations are reused; IV and all-corner scans are optional. "
         "Requires host SICO_PDK_WORKFLOW=generation; normal design cannot publish.",
         {**COMMON, "revision": TEXT}, ["library", "revision"], read=False),
    tool("prepare_pdk_data_update", "PDK generation/maintenance workflow only: preview exact changes to device usage or collected CDF/symbol rules against "
         "the effective standard revision. Requires host SICO_PDK_WORKFLOW=generation. No OA writes and no publication. "
         "For new core CDF policies, first consult the current PDK user manual by device type and discuss its mapping "
         "to actual CDF names/form labels and the user's desired inputs; retain manual locations and unresolved differences. "
         "Use observed CDF keys in patches, not informal aliases or another PDK's family whitelist. "
         "Reuse existing source-valid confirmations without asking again. "
         "Read only the needed records and submit small add/replace/remove object-member patches (arrays replaced whole). The host must obtain a real "
         "user answer covering the returned changes; a model assertion is not confirmation. "
         f"The complete UTF-8 formatted before/after review is limited to {MAX_REVIEW_BYTES} bytes "
         "(not the patch input size). The host displays all differences grouped by file; the question "
         "is only a summary. Over-budget reviews fail before live source validation with actual and "
         "per-file sizes. Split by file or CDF parameter group; confirm and apply each batch, then "
         "read its new revision before preparing the next batch.",
         {**COMMON, "revision": TEXT, "changes": _UPDATE_CHANGES},
         ["library", "revision", "changes"], read=False),
    tool("apply_pdk_data_update", "PDK generation/maintenance workflow only: apply a retained standard-data update only after host-verified user "
         "confirmation. Rechecks the effective revision, dependencies and review evidence, then atomically "
         "publishes a cumulative workspace overlay. No confirmation boolean or user identity is accepted "
         "from the agent. Without a host answer, return the same pending review.",
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
