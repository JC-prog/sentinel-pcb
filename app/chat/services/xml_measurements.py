"""Parses an AOI machine's inspection XML for one flagged feature's measurements, when a case is
created with an XML attached (optional - see graph.py's validate_measurements node). Adapted from
orchestrator-agent/adc_agentic_project's services/dataset_preparation.py (_find_failed_feature,
_extract_measurements) and verification/measurement_validation.py (validate_measurements) - not a
straight port, since there's no CSV row here supplying a requested-inspection-type list
(InspectionDefinition). Every failed inspection under the matched feature is relevant to a
case flagged by a human, not a filtered subset.

Also reused by app/chat/agents/case_agent/mcp_client.py for real AOI/ICT telemetry
(find_failed_feature/extract_all_failed_measurements are non-trivial XML-walking logic, not the
kind of small duplication this repo's "three similar lines" convention condones) - so this module
carries only the trimmed slice of the AOI schema either caller actually needs: Board/Component/
Feature/Inspection identity and status attributes, plus each Measurement's numeric attributes -
never the full multi-thousand-feature panel document.

Never raises: graph.py treats any parse/match failure as a validation issue, not a pipeline error.
"""

from typing import Any
from xml.etree import ElementTree as ET

# Same numeric_keys set as adc_agentic_project/verification/measurement_validation.py.
_NUMERIC_KEYS = {
    "Value",
    "Minimum",
    "Maximum",
    "Target",
    "LowerFailure",
    "UpperFailure",
    "Threshold",
    "HeightAboveLeadPlane",
}


def find_failed_feature(
    root: ET.Element,
    *,
    board_id: str,
    component_ref: str,
    package: str | None,
    feature: str | None,
) -> ET.Element | None:
    """Adapted from dataset_preparation._find_failed_feature - same Board/Component/Package/
    Feature XML matching, minus the CSV-row indirection (board_id/component_ref/package/feature
    come from the flagged case's own identifying fields, not a dataset row). package/feature are
    optional here - when absent, the first matching, failed feature under the matched component
    is used."""

    for board in root.iter("Board"):
        if board.attrib.get("Name", "").strip() != board_id:
            continue
        for component in board.findall("Component"):
            if component.attrib.get("Name", "").strip() != component_ref:
                continue
            xml_pkg = component.attrib.get("Package", "").strip()
            part = component.attrib.get("PartNumber", "").strip()
            if package and xml_pkg and package not in (xml_pkg, part) and not xml_pkg.startswith(
                package
            ):
                continue
            for feature_xml in component.findall("Feature"):
                if feature_xml.attrib.get("FeatureStatus", "").strip().lower() != "failed":
                    continue
                if feature and feature_xml.attrib.get("Identifier", "").strip() != feature:
                    continue
                return feature_xml
    return None


def extract_all_failed_measurements(
    feature_xml: ET.Element,
) -> dict[str, dict[str, dict[str, str]]]:
    """Adapted from dataset_preparation._extract_measurements - collects every Inspection with
    status="Failed" under the matched feature's FeatureResult."""

    result: dict[str, dict[str, dict[str, str]]] = {}
    feature_result = feature_xml.find("FeatureResult")
    if feature_result is None:
        return result

    for inspection in feature_result.findall("Inspection"):
        if inspection.attrib.get("status", "").strip().lower() != "failed":
            continue
        inspection_type = inspection.attrib.get("Type", "").strip()
        measurements_xml = inspection.find("Measurements")
        measurements: dict[str, dict[str, str]] = {}
        if measurements_xml is not None:
            for child in list(measurements_xml):
                measurements[child.tag] = dict(child.attrib)
        result[inspection_type] = measurements

    return result


def validate_measurements(
    measurements_by_inspection: dict[str, dict[str, dict[str, str]]],
) -> dict[str, Any]:
    """Adapted from verification/measurement_validation.py's validate_measurements - same
    numeric_keys set and MALFORMED_NUMERIC/MEASUREMENT_EMPTY issue codes, operating directly on
    the dict extract_all_failed_measurements() returns."""

    if not measurements_by_inspection:
        return {"valid": False, "issues": ["FAILED_INSPECTION_DATA_EMPTY"], "inspection_results": {}}

    issues: list[str] = []
    inspection_results: dict[str, Any] = {}

    for inspection_name, measurements in measurements_by_inspection.items():
        inspection_issues: list[str] = []
        if not measurements:
            inspection_issues.append(f"MEASUREMENT_EMPTY:{inspection_name}")

        for measurement_name, attrs in measurements.items():
            for key, value in attrs.items():
                if key in _NUMERIC_KEYS:
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
            "measurement_count": len(measurements),
        }

    return {"valid": not issues, "issues": issues, "inspection_results": inspection_results}
