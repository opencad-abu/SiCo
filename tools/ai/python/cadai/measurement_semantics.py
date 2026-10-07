"""Circuit-independent measurement meanings, units and data requirements."""


def semantics(kind, operation):
    ac = kind.startswith("ac_")
    paired = kind in {"pair", "ac_transfer", "ac_response"}
    result = dict(
        analysis=dict(
            policy="exact_raw_analysis" if ac else "data_and_units_not_analysis_name",
            required_name="ac" if ac else None,
            condition="Saved AC phasors"
            if ac
            else "Finite real flat waveform on a strictly increasing axis",
        ),
        input=dict(
            representation="cartesian_complex" if ac else "real",
            roles=["input", "output"] if paired else ["signal"],
            axis="nonnegative_frequency" if ac else "strictly_increasing_real",
            source_identity="same_result_test_point_analysis"
            if paired
            else "exact_result_test_point",
            independent_grids=paired,
            family_selection="not_supported",
        ),
        quantities={},
        rules=[
            dict(
                code="no_extrapolation",
                condition="All requested positions must lie in each source range",
            )
        ],
        unsupported=[
            "implicit_family_selection",
            "monte_carlo_aggregation",
            "implicit_spec_limits",
        ],
    )
    q, rules = result["quantities"], result["rules"]
    if not ac:
        if kind == "single":
            q["at" if operation == "sample" else "window.start/stop"] = "source_x_compatible"
            result["output_unit"] = (
                "source_x_compatible" if operation == "crossing" else "source_y_compatible"
            )
            if operation == "crossing":
                q["threshold"] = "source_y_compatible"
                rules.append(
                    dict(
                        code="arrival_crossing",
                        condition="Count arrival at threshold from a strict side; "
                        "starting on threshold is not counted; "
                        "threshold plateau arrival counts once; window stop may count",
                    )
                )
            meanings = dict(
                sample="Linearly interpolated y at explicit x",
                minimum="Minimum y on the closed window, including interpolated endpoints",
                maximum="Maximum y on the closed window, including interpolated endpoints",
                mean="Integral of piecewise-linear y divided by window width; axis-weighted",
                rms="Square root of integral of squared piecewise-linear y divided by "
                "width; includes DC",
                crossing="Axis coordinate of the explicitly selected directional threshold arrival",
            )
        else:
            result["input"]["axis_dimensions"] = ["s", "V", "A", "Hz", "dimensionless"]
            if operation == "delay":
                result["input"]["axis"] = "time"
                result["input"]["axis_dimensions"] = ["s"]
                q.update(
                    {
                        "input_event/output_event.window.start/stop": "time",
                        "input_event.threshold": "input_y_compatible",
                        "output_event.threshold": "output_y_compatible",
                    }
                )
                result["output_unit"] = "time"
                rules.append(
                    dict(
                        code="explicit_event_pairing",
                        condition="Use independently specified threshold arrivals; no "
                        "automatic next edge or positive-delay correction",
                    )
                )
            else:
                result["input"]["amplitude_dimensions"] = ["V", "A", "dimensionless"]
                q["at" if operation == "gain_sample" else "window.start/stop"] = (
                    "common_x_dimension"
                )
                q["denominator_floor"] = "input_y_compatible"
                result["output_unit"] = "dimensionless_1"
                rules += [
                    dict(
                        code="same_amplitude_dimension",
                        condition="V/V, A/A or dimensionless/dimensionless",
                    ),
                    dict(
                        code="denominator_floor",
                        condition="Explicit nonnegative floor; abs(input sample or "
                        "input RMS) must exceed it",
                    ),
                ]
            meanings = dict(
                delay="Signed output event time minus input event time",
                gain_sample="Signed output/input samples at the same physical x",
                gain_rms="RMS(output)/RMS(input) over the same physical window; includes DC",
            )
        result["definition"] = meanings[operation]
        result["interpolation"] = "linear_on_each_original_axis"
        result["unit_conversion"] = "compatible_SI_prefix_or_identical_nonempty_unit"
        if operation not in {"sample", "gain_sample"}:
            rules.append(
                dict(code="increasing_window", condition="Each window start must be less than stop")
            )
        if operation in {"crossing", "delay"}:
            rules.append(
                dict(
                    code="explicit_occurrence",
                    condition="Explicit direction and 1-based occurrence; no fallback",
                )
            )
        return result

    result["input"]["amplitude_dimensions"] = ["V", "A", "dimensionless"]
    result["interpolation"] = "cartesian_linear_frequency_before_division_and_scalar_measurement"
    result["unit_conversion"] = "compatible_SI_prefix; no_RMS_peak_or_dB_linear_conversion"
    q["window.start/stop" if kind == "ac_response" else "at"] = "frequency"
    if paired:
        q["tool.denominator_floor"] = "input_y_compatible"
        rules += [
            dict(
                code="same_amplitude_dimension", condition="V/V, A/A or dimensionless/dimensionless"
            ),
            dict(
                code="denominator_floor",
                condition="Explicit nonnegative floor; input magnitude must exceed it "
                + (
                    "on the entire window including segment interiors"
                    if kind == "ac_response"
                    else "at the requested frequency"
                ),
            ),
        ]
        result["unsupported"] += ["transimpedance", "transconductance"]
    amplitude = "dimensionless_1" if paired else "source_y_compatible"
    if kind != "ac_response":
        result["output_unit"] = (
            amplitude
            if operation == "magnitude"
            else "dB"
            if operation == "magnitude_db"
            else "deg_or_rad"
        )
        result["definition"] = dict(
            magnitude="abs(z)",
            magnitude_db="20*log10(abs(z)/reference)",
            phase="principal arg(z) in (-pi,pi]; negative real axis maps to +pi",
        )[operation]
        result["phasor"] = "output/input" if paired else "absolute_signal"
        if operation == "magnitude_db":
            q["reference"] = amplitude
            rules.append(
                dict(
                    code="positive_db_reference",
                    condition="Explicit reference>0 and abs(z)>0; amplitude convention",
                )
            )
        elif operation == "phase":
            q["magnitude_floor"] = amplitude
            rules.append(
                dict(
                    code="principal_phase",
                    condition="Explicit nonnegative magnitude_floor; abs(z)>floor; no unwrap",
                )
            )
        return result

    rules += [
        dict(
            code="increasing_window", condition="0<=start<stop and full window inside both sources"
        ),
        dict(
            code="strict_crossing",
            condition="Strict side change on Cartesian transfer; tangencies and "
            "window endpoints excluded; plateau is error",
        ),
        dict(
            code="explicit_occurrence",
            condition="Filter direction, then select 1-based occurrence in frequency "
            "order; no fallback",
        ),
    ]
    if operation == "bandwidth":
        q["reference_gain"] = "dimensionless_1"
        result["output_unit"] = "frequency"
        result["definition"] = (
            "Absolute lowpass cutoff where |output/input|=reference_gain*10^(-drop_db/20)"
        )
        rules += [
            dict(
                code="lowpass_cutoff",
                condition="definition=lowpass_cutoff; reference_gain>0; drop_db>0; "
                "direction=falling",
            ),
            dict(
                code="lowpass_window_start",
                condition="Window start gain strictly above the threshold",
            ),
        ]
        result["unsupported"] += [
            "highpass_cutoff",
            "bandpass_width",
            "automatic_passband_reference",
        ]
    else:
        q["magnitude_floor"] = "dimensionless_1"
        result["output_unit"] = "deg_or_rad"
        result["definition"] = (
            "pi + continuous phase of L=loop_sign*output/input at selected |L|=1 crossing"
        )
        rules += [
            dict(
                code="declared_loop",
                condition="Explicit characteristic=one_plus_L and loop_sign; no inference",
            ),
            dict(
                code="phase_branch",
                condition="At window start use principal arg(L)+2*pi*phase_anchor_turns; "
                "continue numerator/denominator paths separately; never wrap the resulting margin",
            ),
            dict(
                code="phase_floor",
                condition="Explicit nonnegative dimensionless floor; |L| exceeds "
                "floor from window start to selected crossing",
            ),
        ]
        result["unsupported"] += [
            "automatic_loop_extraction",
            "stability_certification",
            "inference_of_unsampled_phase_turns",
            "STB",
            "PAC",
        ]
    return result
