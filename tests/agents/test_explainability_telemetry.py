from typing import Any

import pytest

from app.agents.explainability_review_agent.mcp_client import PCBMCPClient

_XML_WITH_HEIGHT_AND_OVERHANG = b"""
<Boards>
  <Board Name="BOARD-1">
    <Component Name="U7" Package="QFN32" PartNumber="QFN32-PN">
      <Feature Identifier="Pad1" FeatureStatus="Failed">
        <FeatureResult>
          <Inspection Type="Height Check" status="Failed" FailedInspectionCriterias="Height">
            <Measurements>
              <ComponentHeight Value="2.1" Minimum="0.0" Maximum="10.0" />
            </Measurements>
          </Inspection>
          <Inspection Type="Overhang Check" status="Failed" FailedInspectionCriterias="Overhang">
            <Measurements>
              <SideOverhang Value="65.0" Minimum="0.0" Maximum="50.0" />
            </Measurements>
          </Inspection>
        </FeatureResult>
      </Feature>
    </Component>
  </Board>
</Boards>
"""

_XML_WITH_MALFORMED_VALUE = b"""
<Boards>
  <Board Name="BOARD-1">
    <Component Name="U7" Package="QFN32" PartNumber="QFN32-PN">
      <Feature Identifier="Pad1" FeatureStatus="Failed">
        <FeatureResult>
          <Inspection Type="Height Check" status="Failed" FailedInspectionCriterias="Height">
            <Measurements>
              <ComponentHeight Value="not-a-number" />
            </Measurements>
          </Inspection>
        </FeatureResult>
      </Feature>
    </Component>
  </Board>
</Boards>
"""


@pytest.fixture
def mcp_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> PCBMCPClient:
    """get_measurements() never touches self.client/self.encoder - stub both out rather than
    loading a real embedded Qdrant client and downloading the real CLIP model for a test that
    doesn't exercise either."""

    monkeypatch.setattr(
        "app.agents.explainability_review_agent.mcp_client.QdrantClient", lambda **kwargs: object()
    )
    monkeypatch.setattr(
        "app.agents.explainability_review_agent.mcp_client.SentenceTransformer",
        lambda *args, **kwargs: object(),
    )
    return PCBMCPClient(qdrant_path=str(tmp_path))


def test_get_measurements_uses_hardcoded_mock_when_no_xml_attached(mcp_client: PCBMCPClient) -> None:
    result = mcp_client.get_measurements("BOARD-1", "U7")
    assert result["laser_profile_height_um"] == 42.5
    assert result["side_overhang_percent"] == 32.0
    assert result["ict_status"] == "PASS"


def test_get_measurements_reads_real_values_from_attached_xml(mcp_client: PCBMCPClient) -> None:
    result = mcp_client.get_measurements(
        "BOARD-1",
        "U7",
        inspection_xml_bytes=_XML_WITH_HEIGHT_AND_OVERHANG,
        package="QFN32",
        feature="Pad1",
    )
    assert result["laser_profile_height_um"] == 2.1
    assert result["side_overhang_percent"] == 65.0
    assert result["ict_status"] == "PASS"


def test_get_measurements_reports_fail_status_on_malformed_numeric_value(
    mcp_client: PCBMCPClient,
) -> None:
    result = mcp_client.get_measurements(
        "BOARD-1", "U7", inspection_xml_bytes=_XML_WITH_MALFORMED_VALUE, package="QFN32", feature="Pad1"
    )
    assert result["ict_status"] == "FAIL"


def test_get_measurements_falls_back_to_mock_when_feature_not_found(
    mcp_client: PCBMCPClient,
) -> None:
    result = mcp_client.get_measurements(
        "BOARD-1",
        "U7",
        inspection_xml_bytes=_XML_WITH_HEIGHT_AND_OVERHANG,
        package="QFN32",
        feature="NoSuchFeature",
    )
    assert result["laser_profile_height_um"] == 42.5
    assert result["side_overhang_percent"] == 32.0


def test_get_measurements_falls_back_to_mock_on_unparseable_xml(mcp_client: PCBMCPClient) -> None:
    result = mcp_client.get_measurements(
        "BOARD-1", "U7", inspection_xml_bytes=b"not xml at all", package="QFN32", feature="Pad1"
    )
    assert result["laser_profile_height_um"] == 42.5
