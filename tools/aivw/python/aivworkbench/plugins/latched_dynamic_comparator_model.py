"""SystemVerilog model rendering for the latched comparator."""

from __future__ import annotations

import re

from .latched_dynamic_comparator_contract import BaselineGenerationRequest

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _sv_name(name: str) -> str:
    if _IDENT.fullmatch(name):
        return name
    if not name or any(character.isspace() for character in name):
        raise ValueError(f"unsupported SystemVerilog identifier: {name!r}")
    return "\\" + name + " "


def _render_model(request: BaselineGenerationRequest) -> str:
    spec = request.spec
    ports = spec["interface"]["ports"]
    order = spec["interface"]["port_order"]
    roles = spec["interface"]["roles"]
    parameters = spec["parameters"]
    module = _sv_name(str(spec["target"]["module"]))
    port_list = ", ".join(_sv_name(str(name)) for name in order)
    declarations: list[str] = []
    for name in order:
        item = ports[name]
        spelling = _sv_name(str(name))
        declarations.append(f"   {item['direction']} {spelling};")
        declarations.append(f"   {item.get('net_type', 'logic')} {spelling};")
    positive_input = _sv_name(str(roles["positive_input"]))
    negative_input = _sv_name(str(roles["negative_input"]))
    positive_output = _sv_name(str(roles["positive_output"]))
    negative_output = _sv_name(str(roles["negative_output"]))
    clock = _sv_name(str(roles["clock"]))
    return f'''import cds_rnm_pkg::*;
`timescale 1ns/1ps

// AI Verification Workbench model-class baseline candidate.
// Source contract SHA256: {request.contract_sha256}
// RNM supplement SHA256: {request.spec_sha256}
// Model version: aivw-m1-rnm-baseline-1
// Generated at: {request.generated_at}
// Behavior is an explicit assumption pending Spectre-to-RNM correlation.
module {module} ({port_list});

{chr(10).join(declarations)}
   parameter real VDD_HIGH = {float(parameters['vdd_high']['value']):.17g};
   parameter real VSS_LOW = {float(parameters['vss_low']['value']):.17g};
   parameter real INPUT_OFFSET = {float(parameters['input_offset']['value']):.17g};
   parameter real DECISION_EPSILON = {float(parameters['decision_epsilon']['value']):.17g};

   real out_state;
   real outb_state;
   real decision;

   initial begin
      out_state = VSS_LOW;
      outb_state = VDD_HIGH;
      decision = 0.0;
   end

   always @(posedge {clock}) begin
      decision = {positive_input} - {negative_input} + INPUT_OFFSET;
      if (decision > DECISION_EPSILON) begin
         out_state = VDD_HIGH;
         outb_state = VSS_LOW;
      end else if (decision < -DECISION_EPSILON) begin
         out_state = VSS_LOW;
         outb_state = VDD_HIGH;
      end else begin
         out_state = (VDD_HIGH + VSS_LOW) / 2.0;
         outb_state = (VDD_HIGH + VSS_LOW) / 2.0;
      end
   end

   assign {positive_output} = out_state;
   assign {negative_output} = outb_state;
endmodule
'''


__all__ = ["_render_model", "_sv_name"]
