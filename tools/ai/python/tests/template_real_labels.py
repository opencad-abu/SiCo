"""Fixed labels reviewed against saved training OA, CDF/model evidence and native PNGs.

These are Codex structural/visual review labels, not independent human expert
acceptance. Source OA, model files, captures and images remain private. This
corpus is development evidence; it is never a held-out family test set.
"""

from cadai.template_schema import digest

VERSION = "training-real-review.20260922.v1"
LABEL_SHA256 = "74f9f6880738e4eab230a361f3e5885cab9680d923dae3ec385fa14aeb57123a"
MODEL_SHA256 = "4cece5fcde03aa0addfd42d5e06d9c270c42dc91f88cd0ec7e4256c98408fcae"
MASTERS = {
    "nmos1v_lvt": ("g45n1lvt", "nmos"),
    "pmos1v_lvt": ("g45p1lvt", "pmos"),
    "nmos1v": ("g45n1svt", "nmos"),
    "pmos1v": ("g45p1svt", "pmos"),
}
# Device rows: exact instance, exact master, D, G, S, B. Deliberately retain
# PM2's body on PM0_B, not VDDA; source facts are not corrected by convention.
CASES = [
    dict(id="pmos_opamp", library="amsDAC", cell="pmos_opamp",
         oa_sha256="03cfb0651214bcc82b8130a9b5b7eab5231b12d27e562d9afbdeacac8b3de5f1",
         devices=[
             "NM0 nmos1v_lvt NM0_D NM0_D VSSA VSSA",
             "NM1 nmos1v_lvt OUTP NM0_D VSSA VSSA",
             "NM2 nmos1v_lvt NM2_D IIN VSSA VSSA",
             "NM3 nmos1v_lvt IIN IIN VSSA VSSA",
             "PM0 pmos1v_lvt NM0_D INP PM0_B PM0_B",
             "PM1 pmos1v_lvt OUTP INN PM0_B PM0_B",
             "PM2 pmos1v_lvt PM0_B NM2_D VDDA PM0_B",
             "PM3 pmos1v_lvt NM2_D NM2_D VDDA VDDA"],
         ports=["IIN input", "INN input", "INP input", "OUTP output",
                "VDDA input", "VSSA input"],
         supply="VDDA", ground="VSSA", changed_pin=("PM2", "B", "VDDA"),
         visual="readable_source; preserve_mirrored_input_pair_and_separate_bias_branch"),
    dict(id="ckbuf_d2s", library="amsSerializer", cell="ckbuf_d2s",
         oa_sha256="7851ff1f2fac69e95ff49f449b21c7b456db7a2de87dd4b1eba0de5ce4aeaf87",
         devices=[
             "NM3 nmos1v NM3_D ck_p avss avss", "NM4 nmos1v NM4_D ck_m avss avss",
             "NM5 nmos1v NM5_D NM4_D avss avss", "NM6 nmos1v cko NM5_D avss avss",
             "PM0 pmos1v NM4_D NM3_D avdd avdd", "PM1 pmos1v NM3_D NM3_D avdd avdd",
             "PM2 pmos1v NM5_D NM4_D avdd avdd", "PM3 pmos1v cko NM5_D avdd avdd"],
         ports=["avdd input", "avss input", "ck_m input", "ck_p input", "cko output"],
         supply="avdd", ground="avss", changed_pin=("NM5", "G", "ck_p"),
         visual="readable_source; preserve_left_input_stage_and_two_right_inverter_columns"),
]
BOUNDARY = dict(
    id="loop_filter", library="amsPLL", cell="loop_filter",
    oa_sha256="21aa1bf57889e059a4ecd57218bf8c7c567abd9eab6b1fa7cb11f4a1bc20610c",
    role="hierarchy", instance="R1", master="resnsnpoly",
    reason="observed_master_schematic; direct_device_promotion_forbidden",
    visual="source_CDF_text_crowding; VDDA_saved_sigType_is_ground_do_not_infer_from_name",
)


def classification(cell):
    model, kind = MASTERS[cell]
    return dict(kind=kind, attributes={}, source_ref="review:ams_gui:model:" + model,
                revision=MODEL_SHA256)


def connections(case):
    return {row.split()[0]: dict(zip(("D", "G", "S", "B"), row.split()[2:]))
            for row in case["devices"]}


def expected_topology(case, *, masters=MASTERS):
    rows = [r.split() for r in case["devices"]]
    nets = sorted({n for r in rows for n in r[2:]})
    return dict(level="direct", gaps=[], net_global_semantics="uniform_boolean_v1",
                devices=[dict(id=r[0], source_name=r[0], kind=masters[r[1]][1],
                              role="device", attributes={},
                              pins=[dict(name=p, net=n) for p, n in
                                    zip(("D", "G", "S", "B"), r[2:])]) for r in rows],
                nets=[dict(id=n, source_name=n, num_bits=1, is_global=False,
                           sig_type="supply" if n == case["supply"] else
                           "ground" if n == case["ground"] else "signal") for n in nets],
                ports=[dict(id="p" + str(i), name=r.split()[0], net=r.split()[0],
                            direction=r.split()[1], num_bits=1)
                       for i, r in enumerate(case["ports"])])


def label_digest():
    return digest(dict(version=VERSION, model=MODEL_SHA256, masters=MASTERS,
                       cases=CASES, boundary=BOUNDARY))
