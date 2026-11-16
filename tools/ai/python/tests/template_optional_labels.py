"""Fixed saved-design labels for structural optional-group review, not simulation approval."""

from template_real_labels import MASTERS, MODEL_SHA256

from cadai.template_schema import digest

VERSION = "training-optional-review.20260922.v1"
LABEL_SHA256 = "5238c2cddfe53ac6c5fba698f89c3f910b54adba0e3ed3375e167a967a57ab6e"
OPAMP = dict(
    id="opamp",
    library="amsDAC",
    cell="opamp",
    oa_sha256="cdfee3360c6518685de4fc34dd7445f3b3f031fc7cf62794da3354e90f683031",
    devices=[
        "NM0 nmos1v_lvt NM0_D INN NM0_S NM0_S",
        "NM1 nmos1v_lvt NM1_D INP NM0_S NM0_S",
        "NM2 nmos1v_lvt NM0_S IIN VSSA VSSA",
        "NM3 nmos1v_lvt IIN IIN VSSA VSSA",
        "NM4 nmos1v_lvt OUTP IIN VSSA VSSA",
        "PM0 pmos1v_lvt NM0_D NM0_D VDDA VDDA",
        "PM1 pmos1v_lvt NM1_D NM0_D VDDA VDDA",
        "PM2 pmos1v_lvt OUTP NM1_D VDDA VDDA",
    ],
    passives={
        "C0": dict(cell="cap", kind="capacitor", pins=dict(PLUS="R2_MINUS", MINUS="OUTP")),
        "R2": dict(cell="res", kind="resistor", pins=dict(PLUS="NM1_D", MINUS="R2_MINUS")),
    },
    ports=["IIN input", "INN input", "INP input", "OUTP output", "VDDA input", "VSSA input"],
    supply="VDDA",
    ground="VSSA",
    optional=dict(
        id="series_compensation",
        devices=["C0", "R2"],
        remove_nets=["R2_MINUS"],
        boundaries=[("NM1", "D", "resistor"), ("PM2", "D", "capacitor")],
        group_pins=[("R2", "PLUS"), ("C0", "MINUS")],
    ),
    purpose=("explicit structural omission only; compensation removal changes stability; "
             "no performance claim"),
)
INVERTER = dict(
    id="inverter",
    library="amsRegInv",
    cell="INVX1",
    oa_sha256="2cf15fd3c76cd94d19c42ee81943df0ba92b9b3f8f7c9f554134097f10ce557c",
    devices=["mn0 nmos1v Y A VSS! VSS!", "mp0 pmos1v Y A VDD! VDD!"],
    passives={},
    ports=["A input", "Y output", "VDD input VDD!", "VSS input VSS!"],
    supply=None,
    ground=None,
    globals=["VDD!", "VSS!"],
    rejection="inherited_connections_not_resolved",
)


def expected_devices(case):
    result = {}
    for row in case["devices"]:
        name, cell, *nets = row.split()
        result[name] = dict(
            library="gpdk045",
            cell=cell,
            kind=MASTERS[cell][1],
            pins=dict(zip(("D", "G", "S", "B"), nets)),
        )
    for name, row in case["passives"].items():
        result[name] = dict(row, library="analogLib")
    return result


def expected_ports(case):
    return {
        r.split()[0]: (r.split()[1], r.split()[-1] if len(r.split()) == 3 else r.split()[0])
        for r in case["ports"]
    }


def expected_nets(case):
    return {
        net: (
            "supply" if net == case["supply"] else "ground" if net == case["ground"] else "signal",
            net in case.get("globals", []),
        )
        for d in expected_devices(case).values()
        for net in d["pins"].values()
    }


def label_digest():
    return digest(
        dict(version=VERSION, model=MODEL_SHA256, masters=MASTERS, cases=[OPAMP, INVERTER])
    )
