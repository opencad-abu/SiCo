"""Bounded HTTP rejection metadata; never reflect provider prose or request values."""

import http.client
import json
import re

MAX_ERROR = 65536
_CODES = frozenset({
    'invalid_request_error', 'invalid_request', 'bad_request', 'invalid_parameter',
    'unsupported_parameter', 'unknown_parameter', 'unsupported_value', 'missing_required_parameter',
    'model_not_found', 'invalid_model', 'unsupported_model', 'unsupported_tool',
    'context_length_exceeded', 'max_tokens_exceeded', 'invalid_api_key',
    'authentication_error', 'permission_denied', 'insufficient_quota', 'rate_limit_exceeded',
    'server_error', 'internal_server_error', 'overloaded_error',
})
_FIELDS = frozenset({
    'model', 'input', 'instructions', 'tools', 'tool_choice', 'parallel_tool_calls',
    'reasoning', 'effort', 'summary', 'text', 'format', 'verbosity', 'type', 'name',
    'namespace', 'function', 'custom', 'parameters', 'strict', 'description',
    'max_output_tokens', 'temperature', 'top_p', 'stream', 'stream_options',
    'include', 'store', 'previous_response_id', 'metadata', 'user', 'service_tier',
    'background', 'truncation', 'content', 'role', 'call_id', 'arguments', 'output',
    'safety_identifier', 'prompt_cache_key', 'prompt_cache_retention',
})


def safe_id(value, secrets=()):
    if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', value)
            or any(secret and secret in value for secret in secrets)):
        return ''
    return value


def safe_parameter(value):
    if not isinstance(value, str) or len(value) > 160:
        return None
    tokens = re.findall(r'[A-Za-z_]+|[0-9]+|[^A-Za-z_0-9.\[\]]', value)
    if (not tokens or any(t not in _FIELDS and not (t.isdigit() and len(t) <= 5) for t in tokens)
            or not re.fullmatch(r'[a-z_]+(?:\.[a-z_]+|\.[0-9]{1,5}|\[[0-9]{1,5}\])*', value)):
        return None
    return value


def fields(raw, secrets=()):
    try:
        value = json.loads(raw)
        error = value.get('error') if isinstance(value, dict) else None
        if not isinstance(error, dict):
            return {}
    except (ValueError, UnicodeError, RecursionError):
        return {}
    result = {}
    for name in ('code', 'type'):
        value = error.get(name)
        if isinstance(value, str) and value in _CODES:
            result[name] = value
    parameter = safe_parameter(error.get('param'))
    if parameter:
        result['param'] = parameter
    # A field equal to a configured credential is still not a diagnostic.
    return {k: v for k, v in result.items() if not any(s and s in v for s in secrets)}


def summary(status, details, prefix='Model gateway returned'):
    text = f'{prefix} HTTP {status}'
    for name in ('code', 'type', 'param', 'request_id'):
        if details.get(name):
            text += f'; {name}={details[name]}'
    return text


def rejection(response, secrets=()):
    """Keep HTTP status even if its optional error body is malformed/truncated."""
    details = {}
    content = response.getheader('Content-Type', '').split(';', 1)[0].strip()
    if content == 'application/json' and response.getheader('Content-Encoding', 'identity') == 'identity':
        try:
            raw = response.read(MAX_ERROR + 1)
            if len(raw) <= MAX_ERROR:
                details = fields(raw, secrets)
        except (OSError, http.client.HTTPException):
            pass
    identity = safe_id(response.getheader('x-request-id', ''), secrets)
    if identity:
        details['request_id'] = identity
    return details


def body(status, details):
    return {'error': dict(details, message=summary(status, details))}
