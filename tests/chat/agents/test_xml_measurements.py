from xml.etree import ElementTree as ET

from app.chat.services.xml_measurements import (
    extract_all_failed_measurements,
    find_failed_feature,
    validate_measurements,
)

_VALID_XML = """
<Boards>
  <Board Name="BOARD-1">
    <Component Name="U7" Package="QFN32" PartNumber="QFN32-PN">
      <Feature Identifier="Pad1" FeatureStatus="Failed">
        <FeatureResult>
          <Inspection Type="Lead Offset" status="Failed" FailedInspectionCriterias="Offset">
            <Measurements>
              <Offset Value="0.5" Minimum="0.0" Maximum="0.3" />
            </Measurements>
          </Inspection>
        </FeatureResult>
      </Feature>
    </Component>
  </Board>
</Boards>
"""


def test_find_failed_feature_matches_board_component_package_feature() -> None:
    root = ET.fromstring(_VALID_XML)
    feature = find_failed_feature(
        root, board_id="BOARD-1", component_ref="U7", package="QFN32", feature="Pad1"
    )
    assert feature is not None
    assert feature.attrib["Identifier"] == "Pad1"


def test_find_failed_feature_returns_none_when_board_does_not_match() -> None:
    root = ET.fromstring(_VALID_XML)
    feature = find_failed_feature(
        root, board_id="NOT-A-BOARD", component_ref="U7", package="QFN32", feature="Pad1"
    )
    assert feature is None


def test_find_failed_feature_ignores_package_feature_when_not_supplied() -> None:
    root = ET.fromstring(_VALID_XML)
    feature = find_failed_feature(
        root, board_id="BOARD-1", component_ref="U7", package=None, feature=None
    )
    assert feature is not None


def test_extract_all_failed_measurements_collects_failed_inspections() -> None:
    root = ET.fromstring(_VALID_XML)
    feature = find_failed_feature(
        root, board_id="BOARD-1", component_ref="U7", package="QFN32", feature="Pad1"
    )
    assert feature is not None
    measurements = extract_all_failed_measurements(feature)
    assert measurements == {"Lead Offset": {"Offset": {"Value": "0.5", "Minimum": "0.0", "Maximum": "0.3"}}}


def test_validate_measurements_passes_on_well_formed_numeric_values() -> None:
    validation = validate_measurements(
        {"Lead Offset": {"Offset": {"Value": "0.5", "Minimum": "0.0", "Maximum": "0.3"}}}
    )
    assert validation["valid"] is True
    assert validation["issues"] == []


def test_validate_measurements_flags_malformed_numeric_value() -> None:
    validation = validate_measurements(
        {"Lead Offset": {"Offset": {"Value": "not-a-number", "Minimum": "0.0"}}}
    )
    assert validation["valid"] is False
    assert "MALFORMED_NUMERIC:Lead Offset.Offset.Value" in validation["issues"]


def test_validate_measurements_flags_empty_measurements() -> None:
    validation = validate_measurements({"Lead Offset": {}})
    assert validation["valid"] is False
    assert "MEASUREMENT_EMPTY:Lead Offset" in validation["issues"]


def test_validate_measurements_flags_completely_empty_input() -> None:
    validation = validate_measurements({})
    assert validation["valid"] is False
    assert validation["issues"] == ["FAILED_INSPECTION_DATA_EMPTY"]
