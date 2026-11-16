"""Compatibility collection entry for licensed circuit creation GUI tests.

Case modules own lifecycle, routing and approved edit contracts; window
presentation cases cover the foreground windows SiCo hands to the user. The
existing fixture module owns prepare, also imported here for project GUI
consumers. Retire this entry only after supported test commands/helper imports
migrate and collection is updated to keep every test exactly once.
"""

# Case filenames do not match pytest's automatic test_* / *_test patterns.
# ruff: noqa: F401

from circuit_create_edit_cases import (
    test_approved_in_place_edit_rebuilds_the_cellview,
    test_rebuild_refuses_to_drop_unlisted_objects,
    test_reroute_one_net_keeps_every_other_wire_and_device,
)
from circuit_create_fixture import prepare
from circuit_create_lifecycle_cases import (
    test_existing_schematic_reuse_continue_replace_and_stale_content,
    test_generic_create_design_instance_tb,
    test_generic_create_native_lifecycle,
)
from circuit_create_routing_cases import (
    test_aligned_port_keeps_the_route_straight,
    test_cadence_route_creates_real_routes_on_primary_pin_centres,
    test_cadence_route_refused_edge_falls_back_to_a_labelled_stub,
    test_outward_port_stubs_never_rotate_the_port_pins,
)
from circuit_window_cases import test_foreground_create_raises_the_opened_window
