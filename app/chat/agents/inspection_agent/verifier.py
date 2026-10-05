"""Verifier sub-agent: is there anything to inspect, and does the attached inspection XML (if any)
hold usable measurements. Two steps, each callable on its own (the ReAct pass in react.py and the
deterministic pipeline in pipeline.py both use them). Deterministic - no model call. Never raises:
a problem becomes `run.error` (unreadable image) or a recorded validation issue (bad XML)."""

from xml.etree import ElementTree as ET

from app.chat.agents.inspection_agent.image_quality import image_quality
from app.chat.agents.inspection_agent.state import InspectionRun
from app.chat.services import xml_measurements


def verify_image(run: InspectionRun) -> None:
    quality = image_quality(run.request.image_bytes)
    if not quality.get("readable"):
        run.fail("IMAGE_UNREADABLE: uploaded file is not a decodable image.")
        return

    run.image_quality = {k: v for k, v in quality.items() if k != "readable"}
    run.note(f"Image verification: readable ({quality['width']}x{quality['height']}).")


def validate_measurements(run: InspectionRun) -> None:
    """A case can still be created from just the image, so an unparseable XML or a feature that
    can't be matched is recorded as a failed validation (which the verdict treats as a reason to
    review) rather than an error. A no-op when no XML was attached."""

    request = run.request
    if request.inspection_xml_bytes is None:
        return

    try:
        root = ET.fromstring(request.inspection_xml_bytes)
    except ET.ParseError:
        run.measurement_validation = {
            "valid": False,
            "issues": ["XML_UNPARSEABLE"],
            "inspection_results": {},
        }
        run.note("Measurement validation: inspection XML was unparseable.")
        return

    feature_xml = xml_measurements.find_failed_feature(
        root,
        board_id=request.board_id,
        component_ref=request.component_ref,
        package=request.package,
        feature=request.feature,
    )
    if feature_xml is None:
        run.measurement_validation = {
            "valid": False,
            "issues": ["FAILED_FEATURE_NOT_FOUND"],
            "inspection_results": {},
        }
        run.note("Measurement validation: no matching failed feature found in the inspection XML.")
        return

    validation = xml_measurements.validate_measurements(
        xml_measurements.extract_all_failed_measurements(feature_xml)
    )
    run.measurement_validation = validation
    run.note(
        f"Measurement validation: {'passed' if validation['valid'] else 'failed'} "
        f"({len(validation['issues'])} issue(s))."
    )
