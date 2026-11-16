import json
import re

import pytest

from cadai.pdk_bridge import PdkBridge
from cadai.pdk_schema import PdkUnavailable


class Client:
    def __init__(self, text):
        self.text = text.encode("utf-8")
        self.calls = []
        self.fail_read = False

    def call(self, method, args):
        assert method == "eval_skill"
        self.calls.append(args["code"])
        code = args["code"]
        if code.startswith("aiPdkCapture"):
            result = {"ok": True, "characters": len(self.text)}
        elif code.startswith("aiPdkRead"):
            if self.fail_read:
                return False, {"code": "bridge_closed", "message": "connection lost"}
            start = int(re.search(r" (\d+)\)$", code).group(1))
            result = {"ok": True, "hex": self.text[start:start + 6000].hex()}
        else:
            result = {"ok": True}
        return True, {"value": json.dumps(result)}


@pytest.mark.parametrize("body", ['{"context":{},"data":"' + ('x' * 20000) + '"}',
                                   '{"context":{},"data":"' + '中文字符' * 3000 + '"}'])
def test_chunk_transfer_round_trip_and_release(body):
    c = Client(body)
    result = PdkBridge(c).capture("device", {"library": "test", "cell": "x"})
    assert result == json.loads(body)
    assert c.calls[-1].startswith("aiPdkDrop")


def test_malformed_transfer_always_releases():
    c = Client("{not json}")
    with pytest.raises(PdkUnavailable, match="not JSON"):
        PdkBridge(c).capture("device", {})
    assert c.calls[-1].startswith("aiPdkDrop")


def test_read_error_cleans_up_without_replaying_capture():
    c = Client("abcd")
    c.fail_read = True
    with pytest.raises(PdkUnavailable, match="lost"):
        PdkBridge(c).capture("device", {})
    assert c.calls[-1].startswith("aiPdkDrop")
    assert sum(code.startswith("aiPdkCapture") for code in c.calls) == 1


def test_replacement_virtuoso_gets_a_fresh_identity_proposal():
    c = Client('{"context":{},"data":{}}')
    bridge = PdkBridge(c)
    bridge.capture("device", {})
    bridge.capture("device", {})
    proposals = [re.search(r'"(session:[a-f0-9]+)"', code).group(1)
                 for code in c.calls if code.startswith("aiPdkCapture")]
    assert len(set(proposals)) == 2
