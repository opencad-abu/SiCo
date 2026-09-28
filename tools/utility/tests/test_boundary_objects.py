"""Object mutation tests exercise actual mixins, decorators and stored graphs."""

from dataclasses import dataclass

from utility.boundary_objects import check_object_sources, composition_errors, object_errors
from conftest import write


TOKEN = dict(source="token.py:Token", public=["session_id", "runtime_id"],
             fields=["session_id", "runtime_id"], frozen=True)


def test_identity_token_cannot_gain_data_storage_or_methods(repo):
    source = "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Token:\n session_id: str\n runtime_id: str\n"
    write(repo, "token.py", source)
    assert not check_object_sources(repo, [TOKEN])
    write(repo, "token.py", source + " controller: object\n")
    assert check_object_sources(repo, [TOKEN])
    write(repo, "token.py", source + " def owner(self): return self._owner\n")
    assert check_object_sources(repo, [TOKEN])
    write(repo, "token.py", source.replace("frozen=True", "frozen=False"))
    assert check_object_sources(repo, [TOKEN])


def test_undeclared_mixin_or_decorator_requires_review(repo):
    write(repo, "port.py", "class Port(BackendMixin):\n def view(self): pass\n")
    contract = dict(source="port.py:Port", public=["view"])
    assert any("base/mixin" in e for e in check_object_sources(repo, [contract]))
    write(repo, "port.py", "@publish_backend\nclass Port:\n def view(self): pass\n")
    assert any("decorator" in e for e in check_object_sources(repo, [contract]))


def test_composed_public_surface_catches_inherited_and_injected_escape():
    class Mixin:
        def owner(self):
            pass

    class Port(Mixin):
        def view(self):
            pass

    assert object_errors(Port(), dict(public=["view"]))

    def publish(cls):
        cls.sessions = property(lambda self: object())
        return cls

    @publish
    class DecoratedPort:
        def view(self):
            pass

    assert object_errors(DecoratedPort(), dict(public=["view"]))


def test_frozen_token_rejects_hidden_controller_storage():
    @dataclass(frozen=True)
    class Token:
        session_id: str
        runtime_id: str

    token = Token("s", "r")
    assert not object_errors(token, TOKEN)
    object.__setattr__(token, "_backend", object())
    assert object_errors(token, TOKEN)


def test_window_graph_inspects_private_collaborators_containers_and_mro():
    contract = dict(ui_module="ui", forbid_attributes=["owner", "sessions"],
                    backend_types=["SessionController"])
    class Window:
        __module__ = "ui.window"

    class SessionController:
        pass

    class Disguised(SessionController):
        pass

    window, component = Window(), Window()
    window.components = [component]
    component.parent = window  # The stored graph may contain cycles.
    assert not composition_errors(window, contract)
    component._cache = {"leak": Disguised()}
    assert any("backend object" in e for e in composition_errors(window, contract))


def test_qualified_owner_does_not_reject_same_named_ui_component():
    contract = dict(ui_module="ui", forbid_attributes=[],
                    backend_types=["service.recovery.SessionRecovery"])
    owner = type("SessionRecovery", (), {"__module__": "service.recovery"})
    component = type("SessionRecovery", (), {"__module__": "ui.recovery"})
    window = component()
    assert not composition_errors(window, contract)
    window._hidden = owner()
    assert composition_errors(window, contract)
