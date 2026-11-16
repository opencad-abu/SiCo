"""Load and enforce the JSON-owned DataHub contract registry."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import FrozenSet, Mapping, Optional, Tuple

import sico_datahub_wire as wire

from .envelope import Envelope
from .errors import ERROR_CODES, HubError
from .limits import MAX_TEXT, check_text, check_value

_NAME = re.compile(r"^sico(?:\.[a-z][a-z0-9_]*)+\.v[1-9][0-9]*$")
_SCHEMA = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\.v[1-9][0-9]*$")
_CONTRACT_FIELDS = {"name", "version", "actions", "limits", "errors", "schema"}
_ACTION_FIELDS = {"capability", "request", "response"}
_REQUEST_FIELDS = {"fields", "required"}
_LIMIT_FIELDS = {"max_items", "max_text", "max_bytes"}


def contract_root() -> Path:
    return Path(__file__).resolve().parents[2] / "datahub" / "contracts"


def _text_tuple(value, field, *, allow_empty=False):
    if not isinstance(value, list) or (not value and not allow_empty):
        raise HubError("invalid_arguments", f"{field} must be a text list")
    result = []
    for item in value:
        check_text(item, field, limit=wire.MAX_ID)
        if item in result:
            raise HubError("invalid_arguments", f"{field} contains a duplicate")
        result.append(item)
    return tuple(result)


@dataclass(frozen=True)
class RequestSpec:
    fields: Tuple[str, ...]
    required: FrozenSet[str]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    capability: str
    request: RequestSpec
    response_schema: str


@dataclass(frozen=True)
class Contract:
    name: str
    version: int
    actions: Mapping[str, ActionSpec]
    schema: Optional[str]
    max_items: int
    max_text: int
    max_bytes: int
    errors: FrozenSet[str]

    @classmethod
    def from_dict(cls, value: dict, *, expected_name: Optional[str] = None):
        if not isinstance(value, dict) or set(value) - _CONTRACT_FIELDS:
            raise HubError("invalid_arguments", "Invalid contract fields")
        required = {"name", "version", "actions", "limits", "errors"}
        if not required <= set(value):
            raise HubError("invalid_arguments", "Contract is missing required fields")
        name = check_text(value["name"], "contract name", limit=wire.MAX_ID)
        if expected_name is not None and name != expected_name:
            raise HubError("invalid_arguments", "Contract filename/name mismatch")
        if not _NAME.fullmatch(name):
            raise HubError("invalid_arguments", "Invalid contract name: " + name)
        version = value["version"]
        if type(version) is not int or version < 1 or not name.endswith(f".v{version}"):
            raise HubError("invalid_arguments", "Contract name/version mismatch")
        limits = value["limits"]
        if not isinstance(limits, dict) or set(limits) != _LIMIT_FIELDS:
            raise HubError("invalid_arguments", "Invalid contract limits")
        for key in _LIMIT_FIELDS:
            item = limits[key]
            if type(item) is not int or item <= 0:
                raise HubError("invalid_arguments", "Invalid contract limit " + key)
        if (
            not 1 <= limits["max_items"] <= wire.MAX_SEQUENCE
            or not 0 < limits["max_text"] <= MAX_TEXT
            or not 0 < limits["max_bytes"] <= wire.MAX_PAYLOAD_BYTES
        ):
            raise HubError("invalid_arguments", "Contract limit exceeds the global bound")
        errors = frozenset(_text_tuple(value["errors"], "contract errors"))
        if errors - frozenset(ERROR_CODES) or "invalid_arguments" not in errors:
            raise HubError("invalid_arguments", "Invalid contract error set")
        schema = value.get("schema")
        if schema is not None:
            check_text(schema, "contract schema", limit=wire.MAX_ID)
            if not _SCHEMA.fullmatch(schema):
                raise HubError("invalid_arguments", "Invalid contract schema")
        actions_raw = value["actions"]
        if not isinstance(actions_raw, dict):
            raise HubError("invalid_arguments", "Contract actions must be an object")
        if not actions_raw and schema is None:
            raise HubError("invalid_arguments", "Actionless contracts require a schema")
        if actions_raw and schema is not None:
            raise HubError("invalid_arguments", "Action contracts cannot define a root schema")
        actions = {}
        for action_name, action_value in actions_raw.items():
            check_text(action_name, "action name", limit=wire.MAX_ID)
            if action_name in actions:
                raise HubError("invalid_arguments", "Duplicate contract action")
            actions[action_name] = _action(action_name, action_value)
        return cls(
            name=name,
            version=version,
            actions=actions,
            schema=schema,
            max_items=limits["max_items"],
            max_text=limits["max_text"],
            max_bytes=limits["max_bytes"],
            errors=errors,
        )


def _action(name, value):
    if not isinstance(value, dict) or set(value) != _ACTION_FIELDS:
        raise HubError("invalid_arguments", "Invalid action fields")
    capability = check_text(value["capability"], "action capability", limit=wire.MAX_ID)
    request = value["request"]
    if not isinstance(request, dict) or set(request) != _REQUEST_FIELDS:
        raise HubError("invalid_arguments", "Invalid action request fields")
    fields = _text_tuple(request["fields"], "request fields")
    required = frozenset(_text_tuple(request["required"], "required request fields"))
    if not required <= set(fields):
        raise HubError("invalid_arguments", "Required request field is not declared")
    response = value["response"]
    if not isinstance(response, dict) or set(response) != {"schema"}:
        raise HubError("invalid_arguments", "Invalid action response fields")
    schema = check_text(response["schema"], "response schema", limit=wire.MAX_ID)
    if not _SCHEMA.fullmatch(schema):
        raise HubError("invalid_arguments", "Invalid response schema")
    return ActionSpec(name, capability, RequestSpec(fields, required), schema)


@dataclass(frozen=True)
class Registry:
    contracts: Mapping[str, Contract]

    @classmethod
    def from_directory(cls, directory=None):
        root = Path(directory) if directory is not None else contract_root()
        if not root.is_dir():
            raise HubError("internal", "DataHub contract directory is unavailable")
        contracts = {}
        for path in sorted(root.glob("*.json")):
            try:
                value = wire.loads(path.read_bytes())
            except (OSError, HubError) as exc:
                raise HubError("internal", "Contract file could not be read: " + path.name) from exc
            contract = Contract.from_dict(value, expected_name=path.stem)
            if contract.name in contracts:
                raise HubError("invalid_arguments", "Duplicate contract: " + contract.name)
            contracts[contract.name] = contract
        if not contracts:
            raise HubError("internal", "DataHub contract registry is empty")
        return cls(contracts)

    def names(self) -> Tuple[str, ...]:
        return tuple(sorted(self.contracts))

    def get(self, name: str, version: int) -> Contract:
        contract = self.contracts.get(name)
        if contract is None:
            raise HubError("unsupported_contract", "Unsupported DataHub contract: " + str(name))
        if contract.version != version:
            raise HubError(
                "version_mismatch",
                f"Contract {name} version {version} is not supported",
                {"expected_version": contract.version},
            )
        return contract

    def action(self, name: str, version: int, action: str, capabilities=None) -> Tuple[Contract, ActionSpec]:
        contract = self.get(name, version)
        spec = contract.actions.get(action)
        if spec is None:
            raise HubError("unsupported_contract", "Unsupported contract action: " + str(action))
        if capabilities is not None and spec.capability not in set(capabilities):
            raise HubError(
                "capability_not_available",
                "Required capability is unavailable: " + spec.capability,
                {"capability": spec.capability},
            )
        return contract, spec

    def validate_request(self, envelope: Envelope, capabilities=None) -> ActionSpec:
        if not isinstance(envelope, Envelope):
            raise HubError("invalid_arguments", "Expected a DataHub envelope")
        contract, spec = self.action(
            envelope.contract, envelope.version, envelope.action, capabilities
        )
        if envelope.page is not None:
            raise HubError("invalid_arguments", "Request envelopes cannot carry a page")
        fields = set(envelope.payload)
        unknown = fields - set(spec.request.fields)
        missing = set(spec.request.required) - fields
        if unknown or missing:
            raise HubError(
                "invalid_arguments",
                "Request payload fields do not match the contract",
                {"unknown": sorted(unknown), "missing": sorted(missing)},
            )
        check_value(envelope.payload)
        if "limit" in fields:
            limit = envelope.payload["limit"]
            if type(limit) is not int or not 1 <= limit <= contract.max_items:
                raise HubError("invalid_arguments", "Request limit is outside the contract")
        if "offset" in fields:
            offset = envelope.payload["offset"]
            if type(offset) is not int or not 0 <= offset <= 4096:
                raise HubError("invalid_arguments", "Request offset is outside the contract")
        return spec


@lru_cache(maxsize=1)
def default_registry() -> Registry:
    return Registry.from_directory()


__all__ = [
    "ActionSpec",
    "Contract",
    "Registry",
    "RequestSpec",
    "contract_root",
    "default_registry",
]
