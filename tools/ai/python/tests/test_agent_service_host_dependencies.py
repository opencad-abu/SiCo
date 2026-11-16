"""Host launch settings cannot hide an existing owner of a captured design."""

import pytest
from agent_service_business_fixtures import business_service, exchange, open_request
from sico.agent_resources import data_sources, model
from sico.core.contracts import BoundContext
from sico.service.service_messages import ServiceRequest
from sico.storage.roots import agent_root
from sico.transport.bridge_client import BridgeClient
from test_agent_service_host import host_bridge, payload, query, submit, target


def test_schematic_ade_layout_repeat_keeps_one_owner_and_each_source(tmp_path, monkeypatch):
    contexts = []
    for index, view in enumerate(('schematic', 'maestro', 'layout', 'maestro',
                                  'layout', 'schematic')):
        number = {'schematic': 2, 'maestro': 3, 'layout': 5}[view]
        context = target(tmp_path, 'ask-' + str(index), view=view)
        contexts.append(BoundContext(context.instance_id, context.generation,
            context.target_id, dict(context.snapshot, window='window:' + str(number),
                window_ref='wctx_fixture_' + str(number),
                ade_session='fnxSession0' if view == 'maestro' else '',
                entry={'schematic': 'schematic_rmb', 'maestro': 'ade_menu',
                       'layout': 'layout_rmb'}[view])))
    with host_bridge(tmp_path, contexts[0]) as (bridge, client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        address = None
        for context in contexts:
            result = submit(service, client, context, text=context.target_id)
            assert result.outcome == 'accepted'
            address = address or result.address
            assert result.address == address
            controller = service.owner.controllers[address.session.session_id]
            assert controller.wait(3)
            assert query(service, client, context.target_id).execution == 'completed'
        assert len(service.owner.controllers) == 1
        with bridge.broker._changed:
            state = bridge.broker.bindings.sessions[controller.session_id]
            assert not state.invalidated
            assert set(bridge.broker.bindings.owners.values()) == {controller.session_id}
            assert all(state.targets[item.target_id] == item for item in contexts)
        assert [row['payload']['context'] for row in controller.journal.events()
                if row['kind'] == 'task.started'] == [item.record() for item in contexts]
        assert service.provider.seen == [item.target_id for item in contexts]


@pytest.mark.parametrize("capture", ["path_alias", "project_data", "project_model"])
def test_repeat_ask_reuses_owner_after_environment_capture(tmp_path, monkeypatch, capture):
    data = tmp_path / "data"
    data.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(data, target_is_directory=True)
    environment = {}
    if capture == "path_alias":
        environment = {"SICO_PDK_DATA": str(alias),
                       "SICO_CIRCUIT_TEMPLATES_DIR": str(alias)}
    elif capture == "project_data":
        data_sources.save(tmp_path, str(data), str(data), expected_revision=None)
    else:
        model.save(tmp_path, "python", "openai", expected_revision=None)
    first_context = target(tmp_path, "first", view="maestro")
    with host_bridge(tmp_path, first_context) as (_, client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        first = submit(service, client, first_context, environment=environment, text="first")
        assert first.outcome == "accepted"
        controller = service.owner.controllers[first.address.session.session_id]
        assert controller.wait(3)
        dependencies = service.owner.dependencies_for(controller.session_id)
        assert dependencies.environment != environment  # Exercise the production mismatch.
        contexts = [first_context]
        for index, view in enumerate(("schematic", "layout", "maestro")):
            context = target(tmp_path, "again-" + str(index), view=view)
            contexts.append(context)
            again = submit(service, client, context, environment=environment,
                           text=context.target_id)
            assert again.outcome == "accepted"
            assert again.address == first.address
            assert controller.wait(3)
            assert query(service, client, context.target_id).execution == "completed"
        assert service.provider.seen == [context.target_id for context in contexts]
        assert [row["payload"]["context"] for row in controller.journal.events()
                if row["kind"] == "task.started"] == [context.record() for context in contexts]
        assert list(service.owner.controllers) == [controller.session_id]
        assert {path.name for path in (agent_root(tmp_path) / "sessions").iterdir()
                if path.is_dir()} == {controller.session_id}


def test_preexisting_split_view_owners_are_not_silently_merged(tmp_path, monkeypatch):
    initial = target(tmp_path, "initial", view="maestro")
    with host_bridge(tmp_path, initial) as (bridge, client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        addresses = {}
        for view in ("maestro", "schematic", "layout"):
            context = target(tmp_path, "open-" + view, view=view)
            client.register_target(context)
            opened = exchange(service, open_request(view, context=context,
                bridge=client.descriptor, environment={"LAUNCH_SOURCE": view}))
            assert opened.outcome == "opened"
            addresses[view] = opened.address
        for index, view in enumerate(("maestro", "schematic", "layout", "maestro")):
            controller = service.owner.controllers[view]
            key = "repeat-" + str(index)
            context = target(tmp_path, key, view=view)
            before = {name: item.journal.events() for name, item
                      in service.owner.controllers.items() if name != view}
            result = submit(service, client, context, text=key,
                            environment={"LAUNCH_SOURCE": "quick-input"})
            assert result.outcome == "accepted" and result.address == addresses[view]
            assert controller.wait(3)
            for name, events in before.items():
                assert service.owner.controllers[name].journal.events() == events
            with bridge.broker._changed:
                bridge.broker.bindings.require(view, context)
        assert set(service.owner.controllers) == set(addresses)
        assert len(service.provider.seen) == 4


def test_existing_owner_keeps_configuration_and_new_design_captures_new_inputs(
        tmp_path, monkeypatch):
    initial = target(tmp_path, "first")
    with host_bridge(tmp_path, initial) as (_, client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        def ask(context, selection):
            client.register_target(context)
            row = payload(context, text=context.target_id,
                          environment={"LAUNCH_SOURCE": selection})
            row["provider_config"] = {"model": selection}
            reference = client.stage_host_payload(row)
            return exchange(service, ServiceRequest("host.submit", context.target_id,
                dict(bridge=client.descriptor, **reference)))

        first = ask(initial, "original")
        assert first.outcome == "accepted"
        controller = service.owner.controllers[first.address.session.session_id]
        assert controller.wait(3)
        dependencies = service.owner.dependencies_for(controller.session_id)
        again_context = target(tmp_path, "again")
        again = ask(again_context, "changed")
        assert again.outcome == "accepted" and again.address == first.address
        assert controller.wait(3)
        assert service.owner.dependencies_for(controller.session_id) is dependencies
        assert dependencies.environment["LAUNCH_SOURCE"] == "original"
        assert dependencies.provider_config == {"model": "original"}
        other = ask(target(tmp_path, "other", cell="Other"), "changed")
        assert other.outcome == "accepted" and other.address != first.address
        other_controller = service.owner.controllers[other.address.session.session_id]
        assert other_controller.wait(3)
        other_dependencies = service.owner.dependencies_for(other_controller.session_id)
        assert other_dependencies.environment["LAUNCH_SOURCE"] == "changed"
        assert other_dependencies.provider_config == {"model": "changed"}
        assert ask(again_context, "changed").address == first.address
        assert service.provider.seen == ["first", "again", "other"]
        assert len(service.owner.controllers) == 2
