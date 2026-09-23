from pathlib import Path
import tempfile
import pandas as pd
from services.dataset_preparation import DatasetPreparationService

def test_pipe_separated_inspection_definition():
    xml = """<Result>
  <Board Name="Board1">
    <Component Name="C1" Package="PKG1">
      <Feature Identifier="Body" FeatureStatus="Failed">
        <FeatureResult>
          <Inspection Type="AI2" status="Failed" FailedInspectionCriterias="OffsetY,Offset">
            <Measurements><X Value="0.1"/><Y Value="-0.2"/></Measurements>
          </Inspection>
          <Inspection Type="BlobDetection" status="Failed" FailedInspectionCriterias="MaxLengthAmongBlobs">
            <Measurements><MaxBlobLength Value="0.08"/></Measurements>
          </Inspection>
        </FeatureResult>
      </Feature>
    </Component>
  </Board>
</Result>"""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        csv_path = td/"dataset.csv"
        xml_path = td/"inspection.xml"
        pd.DataFrame([{
            "SampleID":"S1","Board":"Board1","Package":"PKG1","Component":"C1",
            "InspectionDefinition":"AI2|BlobDetection","Timestamp":"20260908",
            "Feature":"Body","Defect":"Shift","GoldenImage":"Golden.jpg","DefectImage":"Defect.jpg"
        }]).to_csv(csv_path,index=False)
        xml_path.write_text(xml,encoding="utf-8")
        r=DatasetPreparationService().prepare(csv_path,xml_path)
        s=r.data["samples"][0]
        assert s["preparation_status"]=="READY"
        assert s["inspection_definitions"]==["AI2","BlobDetection"]
        assert set(s["failed_inspections"])=={"AI2","BlobDetection"}

def test_missing_one_failed_inspection():
    xml = """<Result>
  <Board Name="Board1">
    <Component Name="C1" Package="PKG1">
      <Feature Identifier="Body" FeatureStatus="Failed">
        <FeatureResult>
          <Inspection Type="AI2" status="Failed"><Measurements><X Value="0.1"/></Measurements></Inspection>
          <Inspection Type="BlobDetection" status="Passed"><Measurements><MaxBlobLength Value="0.02"/></Measurements></Inspection>
        </FeatureResult>
      </Feature>
    </Component>
  </Board>
</Result>"""
    with tempfile.TemporaryDirectory() as td:
        td=Path(td)
        csv_path=td/"dataset.csv"; xml_path=td/"inspection.xml"
        pd.DataFrame([{
            "SampleID":"S1","Board":"Board1","Package":"PKG1","Component":"C1",
            "InspectionDefinition":"AI2|BlobDetection","Timestamp":"20260908",
            "Feature":"Body","Defect":"Shift","GoldenImage":"Golden.jpg","DefectImage":"Defect.jpg"
        }]).to_csv(csv_path,index=False)
        xml_path.write_text(xml,encoding="utf-8")
        s=DatasetPreparationService().prepare(csv_path,xml_path).data["samples"][0]
        assert s["preparation_status"]=="FAILED"
        assert s["missing_failed_inspections"]==["BlobDetection"]
