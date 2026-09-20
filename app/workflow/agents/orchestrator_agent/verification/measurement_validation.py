"""Ported verbatim from orchestrator-agent/adc_agentic_project's verification/measurement_validation.py."""

def validate_measurements(sample):
    """Validate measurements separately for each failed inspection definition."""
    failed_inspections = sample.get("failed_inspections") or {}
    if not failed_inspections:
        return {"valid": False, "issues": ["FAILED_INSPECTION_DATA_EMPTY"], "inspection_results": {}}

    numeric_keys = {
        "Value", "Minimum", "Maximum", "Target", "LowerFailure", "UpperFailure",
        "Threshold", "HeightAboveLeadPlane"
    }
    issues, inspection_results = [], {}

    for inspection_name, data in failed_inspections.items():
        inspection_issues = []
        measurements = data.get("measurements") or {}
        if not measurements:
            inspection_issues.append(f"MEASUREMENT_EMPTY:{inspection_name}")

        for measurement_name, attrs in measurements.items():
            for key, value in attrs.items():
                if key in numeric_keys:
                    try:
                        float(value)
                    except (TypeError, ValueError):
                        inspection_issues.append(
                            f"MALFORMED_NUMERIC:{inspection_name}.{measurement_name}.{key}"
                        )

        issues.extend(inspection_issues)
        inspection_results[inspection_name] = {
            "valid": not inspection_issues,
            "issues": inspection_issues,
            "failed_criteria": data.get("failed_criteria", []),
            "measurement_count": len(measurements),
        }

    return {"valid": not issues, "issues": issues, "inspection_results": inspection_results}
