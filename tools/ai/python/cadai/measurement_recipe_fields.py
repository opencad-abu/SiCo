"""Operation fields shared by execution validators and capability discovery."""


def recipe_fields(kind, operation):
    fields = {"id", "operation", "interpolation", "result_unit"}
    if kind == "single":
        fields |= {"at"} if operation == "sample" else {"window"}
        if operation == "crossing":
            fields |= {"threshold", "direction", "occurrence"}
    elif kind == "pair":
        fields |= (
            {"pairing", "input_event", "output_event"}
            if operation == "delay"
            else {"denominator_floor", "at" if operation == "gain_sample" else "window"}
        )
    elif kind in {"ac_signal", "ac_transfer"}:
        fields.add("at")
        if operation == "phase":
            fields |= {"phase_mode", "magnitude_floor"}
        elif operation == "magnitude_db":
            fields.add("reference")
    elif kind == "ac_response":
        fields |= {"window", "direction", "occurrence"}
        fields |= (
            {"reference_gain", "drop_db", "definition"}
            if operation == "bandwidth"
            else {
                "characteristic",
                "loop_sign",
                "phase_anchor_turns",
                "phase_mode",
                "magnitude_floor",
            }
        )
    else:
        raise ValueError("unknown measurement kind")
    return fields
