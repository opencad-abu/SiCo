"""Documented row failure patterns and tool rejection classes, independent of fixtures."""


def failures(kind, operation):
    ac = kind.startswith("ac_")
    paired = kind in {"pair", "ac_transfer", "ac_response"}
    prefix = "{role}:" if paired or ac else ""
    missing = {
        prefix + "source_{status}": "Source is not a usable waveform; status is retained",
        prefix + "empty_waveform": "Source contains no samples",
    }
    errors = {
        "unit_unknown": "A quantity unit is missing",
        "unit_incompatible": "Requested unit conversion is not supported",
        "numeric_overflow": "A finite result cannot be represented",
    }
    if kind == "ac_response":
        missing.update(
            window_out_of_range="Full requested window is unavailable",
            requested_crossing_not_found=(
                "No crossing matches the requested direction and occurrence"
            ),
        )
        errors.update(
            negative_frequency="Negative frequency bound",
            invalid_window="start is not less than stop",
            frequency_axis_unit_incompatible="Source x unit is not frequency",
            frequency_unit_incompatible="Requested window unit is not frequency",
            phasor_unit_incompatible="Source y dimension is unsupported",
            transfer_unit_incompatible="Input and output dimensions differ",
            frequency_precision_loss="Unit conversion collapses frequency ordering",
            union_grid_limit="Combined source nodes exceed the sample limit",
            denominator_at_or_below_floor_in_window=(
                "Input magnitude reaches floor anywhere in window"
            ),
            threshold_numeric_range="Crossing threshold is nonpositive or nonfinite",
            threshold_plateau="A complete segment lies on the threshold",
            crossing_numerically_ambiguous="Finite precision cannot resolve the crossing",
            crossing_count_limit="Too many crossings to preserve completely",
            crossing_residual_too_large="Selected root fails the magnitude residual check",
        )
        if operation == "bandwidth":
            errors["lowpass_window_start_not_above_threshold"] = (
                "Window does not start above the declared cutoff level"
            )
        else:
            errors.update(
                phase_unit_must_be_deg_or_rad="Invalid margin unit",
                phase_magnitude_at_or_below_floor_in_path=(
                    "Loop phase path reaches its floor before the selected crossing"
                ),
            )
    elif ac:
        missing[prefix + "out_of_range"] = "Requested frequency unavailable in this source"
        errors.update(
            frequency_axis_unit_incompatible="Source axis unit is not frequency",
            frequency_unit_incompatible="Requested frequency unit incompatible",
            phasor_unit_incompatible="Source amplitude dimension unsupported",
        )
        if paired:
            errors.update(
                transfer_unit_incompatible="Input and output dimensions differ",
                denominator_at_or_below_floor=(
                    "Input magnitude reaches floor at requested frequency"
                ),
            )
        if operation == "magnitude_db":
            errors.update(
                db_result_unit_required="Result unit is not dB",
                db_requires_positive_magnitude_and_reference="Magnitude/reference not positive",
            )
        if operation == "phase":
            errors.update(
                phase_unit_must_be_deg_or_rad="Invalid phase unit",
                phase_magnitude_at_or_below_floor="Phase magnitude reaches floor",
            )
    else:
        missing[prefix + "out_of_range"] = "Requested position/window unavailable"
        if operation not in {"sample", "gain_sample"}:
            errors[prefix + "invalid_window"] = "Window start is not less than stop"
        if operation in {"crossing", "delay"}:
            missing[prefix + "no_crossing"] = "Requested threshold arrival not found"
        if paired:
            errors.update(
                axis_unit_unknown="Source axis unit missing",
                axis_unit_incompatible="Axis units/dimensions not supported for pairing",
            )
            if operation == "delay":
                errors["delay_requires_time_axis"] = "Delay requires a time axis"
            else:
                errors.update(
                    gain_unit_unknown="Amplitude unit missing",
                    gain_unit_incompatible="Gain requires compatible V, A or dimensionless data",
                    gain_result_unit_must_be_1="Real gain result must use unit 1",
                    denominator_at_or_below_floor="Input sample or RMS reaches floor",
                )
            errors.update(
                {
                    "{role}:unit_unknown": "Constituent quantity unit missing",
                    "{role}:unit_incompatible": "Constituent quantity unit incompatible",
                    "{role}:numeric_overflow": "Constituent numeric result out of range",
                }
            )
    return dict(
        row_patterns=[
            dict(status=status, reason_pattern=code, condition=message)
            for status, values in [("missing", missing), ("error", errors)]
            for code, message in sorted(values.items())
        ],
        pattern_variables=dict(
            role=["input", "output"] if paired else ["signal"],
            status=["scalar", "complex", "family", "missing", "error"],
        ),
        tool_rejection_classes=[
            "invalid_or_inapplicable_recipe_fields",
            "duplicate_recipe_id",
            "invalid_artifact_or_source_identity",
            "unsupported_or_malformed_samples",
            "resource_limit_or_invalid_reference",
        ],
        policy="missing/error rows have value=null; tool rejections produce no measurement report",
    )
