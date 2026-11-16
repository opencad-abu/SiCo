"""Relay framing and cross-pipe ordering without a sandbox process."""

import os
from types import SimpleNamespace

import pytest

from sico.execution import relay_wire as wire
from sico.execution.bash_relay import BashRelay
from sico.terminal.bash_control import BashControl


def test_ready_output_waits_for_started_control_even_after_permit(monkeypatch):
    import sico.execution.bash_relay as module
    relay = object.__new__(BashRelay)
    relay.shell = SimpleNamespace(pid=123)
    relay.control_read = 100
    relay.decoder = BashControl()
    relay.decoder.expect('command')
    facts = iter(b'\0'.join([kind, b'command', number, b'/work', b''])
                 for kind, number in [(b'prepared', b'1'), (b'started', b'1'), (b'finished', b'0')])
    observed, output = [], []
    def observe(fact, pipes):
        observed.append(fact.kind)
        if fact.kind == 'finished': pipes.ended = True
    class Pipes:
        ended = False
        sent = set()
        def release_writers(self): self.ended = True
        def scope_ended(self, pid): return self.ended
        def read(self, stream):
            assert 'started' in observed, 'Output read before started control was consumed'
            if stream not in self.sent:
                self.sent.add(stream)
                return b'early'
            return b'' if self.ended else None
    monkeypatch.setattr(module.os, 'read', lambda *args: next(facts))
    monkeypatch.setattr(module.select, 'select', lambda *args: ([], [], []))
    relay._observation = observe
    relay.emit = lambda *args, **kwargs: output.append((args, kwargs))
    relay._drain('command', Pipes())
    assert [item[0][0] for item in output] == ['output', 'output', 'complete']


def test_frame_limit_truncation_and_consecutive_frames():
    value = dict(command='x' * (wire.MAX_FRAME - 100))
    encoded = wire.encode(value)
    decoder = wire.decoder()
    assert decoder.feed(encoded[:3]) == []
    assert decoder.feed(encoded[3:]) == [value]
    with pytest.raises(ValueError):
        wire.encode(dict(command='x' * wire.MAX_FRAME))
    read, write = os.pipe()
    try:
        os.write(write, wire.encode(dict(kind='first')) + wire.encode(dict(kind='second')))
        assert wire.receive(read, timeout=1) == dict(kind='first')
        assert wire.receive(read, timeout=1) == dict(kind='second')
        os.write(write, b'\0\0')
        os.close(write)
        write = None
        with pytest.raises(EOFError): wire.receive(read, timeout=1)
    finally:
        os.close(read)
        if write is not None: os.close(write)


def test_scope_release_does_not_race_the_final_control_read(monkeypatch):
    import sico.execution.bash_relay as module
    relay = object.__new__(BashRelay)
    relay.shell = SimpleNamespace(pid=123)
    relay.control_read = 100
    relay.decoder = BashControl()
    relay.decoder.expect('command')
    reads = iter([b'started\0command\0' + b'123\0/\0', None,
                  b'finished\0command\0' + b'7\0/\0'])
    seen = []
    class Pipes:
        ended = False
        scopes = iter([False, True, True])
        def observe_open(self): pass
        def scope_ended(self, pid): return next(self.scopes)
        def release_writers(self): self.ended = True
        def read(self, stream): return b'' if self.ended else None
    def read(*args):
        data = next(reads)
        if data is None: raise BlockingIOError()
        return data
    relay.emit = lambda *args, **kwargs: seen.append((args, kwargs))
    monkeypatch.setattr(module.os, 'read', read)
    monkeypatch.setattr(module.select, 'select', lambda *args: ([], [], []))
    relay._drain('command', Pipes())
    assert seen == [(('started', 'command'), {}),
                    (('complete', 'command'), {'exit_code': 7})]


def test_failed_scope_observation_drains_available_tail_but_never_completes(monkeypatch):
    import sico.execution.bash_relay as module
    relay = object.__new__(BashRelay)
    relay.shell = SimpleNamespace(pid=123)
    relay.control_read = 100
    relay.decoder = BashControl()
    relay.decoder.expect('command')
    seen = []
    reads = iter([b'started\0command\0' + b'123\0/\0', None])
    class Pipes:
        sent = False
        def observe_open(self): pass
        def scope_ended(self, pid): raise PermissionError('injected unreadable /proc')
        def release_writers(self): pass
        def read(self, stream):
            if stream == 'stdout' and not self.sent:
                self.sent = True
                return b'tail'
            return None
    def read(*args):
        data = next(reads)
        if data is None: raise BlockingIOError()
        return data
    relay.emit = lambda *args, **kwargs: seen.append((args, kwargs))
    monkeypatch.setattr(module.os, 'read', read)
    monkeypatch.setattr(module.select, 'select', lambda *args: ([], [], []))
    assert relay._drain('command', Pipes()) is False
    assert [row[0][0] for row in seen] == ['started', 'output', 'failed']
    assert seen[-2][1] == dict(stream='stdout', data='dGFpbA==')
