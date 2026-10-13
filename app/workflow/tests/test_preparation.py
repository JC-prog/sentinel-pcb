from pathlib import Path
from services.dataset_preparation import DatasetPreparationService

def test_real_sample_xml_matching():
    root = Path(__file__).resolve().parents[1]
    r = DatasetPreparationService().prepare(
        root/"sample_data/dataset.csv",
        root/"sample_data/inspection.xml"
    )
    assert r.metrics["total_samples"] > 0
    assert r.metrics["ready_samples"] > 0
