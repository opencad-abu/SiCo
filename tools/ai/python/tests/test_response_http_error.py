"""Provider error prose, arbitrary fields and malformed bodies stay private."""
import http.client
import json
from types import SimpleNamespace

import pytest
from cadai.response_http_error import fields, rejection, safe_parameter


@pytest.mark.parametrize('param', ['tools[12].type', 'tools.0.parameters', 'reasoning.effort', 'model'])
def test_known_parameter_names_only(param):
    raw = json.dumps({'error': {'code': 'unsupported_parameter', 'param': param,
                               'message': 'private prompt', 'type': 'secret'}})
    assert fields(raw) == {'code': 'unsupported_parameter', 'param': param}


@pytest.mark.parametrize('param', ['tools[0].private_token', 'secret', 'input.private_design',
                                  'tools[999999999].type', 'model\nsecret', None])
def test_reject_arbitrary_parameter_values(param):
    assert safe_parameter(param) is None


def test_unknown_or_credential_fields_are_not_reflected():
    assert fields(json.dumps({'error': {'code': 'private', 'param': 'model'}}), ('model',)) == {}
    assert fields(b'not json') == {}
    assert fields(b'null') == {}
    assert fields(json.dumps({'error': 'secret'})) == {}
    assert fields(b'{"error":{"message":"secret","code":[]}}') == {}


@pytest.mark.parametrize('kind', ['html', 'large', 'truncated', 'timeout'])
def test_optional_error_body_cannot_hide_status(kind):
    read_sizes = []
    def read(size):
        read_sizes.append(size)
        if kind == 'truncated':
            raise http.client.IncompleteRead(b'secret')
        if kind == 'timeout':
            raise TimeoutError('secret')
        return b'x' * size
    response = SimpleNamespace(read=read, getheader=lambda name, default='': {
        'Content-Type': 'text/html' if kind == 'html' else 'application/json',
        'x-request-id': 'request-safe',
    }.get(name, default))
    assert rejection(response) == {'request_id': 'request-safe'}
    assert read_sizes == ([] if kind == 'html' else [65537])
