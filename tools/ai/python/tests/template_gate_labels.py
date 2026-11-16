"""Fixed Custom IC source labels: explicit local-supply gates, Codex review only."""

from cadai.template_schema import digest

VERSION = "training-gate-review.20260922.v1"
LABEL_SHA256 = "64df9949b4b8ce375f59eea1f1c71a609a6d216c80245d1d8a906173edbed876"
MODEL_SHA256 = "cfae85bc6d3db866e13d8e7d0b02cf5ab5f63803d0d11fa68c0f50d92dd580bb"
LIBRARY = "gpdk090"
MASTERS = {"nmos2v": ("gpdk090_nmos2v", "nmos"),
           "pmos2v": ("gpdk090_pmos2v", "pmos")}
PARAMETERS = {"nmos": dict(l="280n", w="1u", fingers="1", m="1"),
              "pmos": dict(l="280n", w="2u", fingers="1", m="1")}
CASES = [
    dict(id="adc_invertx1", library="ether", cell="adc_invertx1",
         oa_sha256="50e65009c609d38accb21a2aceaced667f7f2fe9f246150ea81e70f756296d19",
         devices=["NM0 nmos2v OUT IN VSS VSS", "PM0 pmos2v OUT IN VDD VDD"],
         ports=["IN input", "OUT output", "VDD input", "VSS input"],
         supply=None, ground=None, changed_pin=("NM0", "B", "VDD"),
         visual="PMOS_above_NMOS; local_supply_labels_overlap_source_parameter_text"),
    dict(id="adc_nandx3", library="ether", cell="adc_nandx3",
         oa_sha256="fccbfc0211f26fd644e132e83d81915bb0bd1210a7d0c5c550cd71c0ff20b5ac",
         devices=["NM0 nmos2v OUT A net6 VSS", "NM1 nmos2v net6 B VSS VSS",
                  "PM0 pmos2v OUT A VDD VDD", "PM1 pmos2v OUT B VDD VDD"],
         ports=["A input", "B input", "OUT output", "VDD input", "VSS input"],
         supply=None, ground=None, changed_pin=("NM0", "B", "net6"),
         visual="two_top_PMOS; serial_NMOS_column; source_bulk_labels_overlap_CDF_width"),
]


def classification(cell):
    model, kind = MASTERS[cell]
    return dict(kind=kind, attributes={}, source_ref="review:custom_ic:model:" + model,
                revision=MODEL_SHA256)


def label_digest():
    return digest(dict(version=VERSION, model=MODEL_SHA256, library=LIBRARY,
                       masters=MASTERS, parameters=PARAMETERS, cases=CASES))
