from app.agents.dataset_preparation import prepare_sample


def test_prepare_sample_assembles_fields() -> None:
    sample = prepare_sample(
        workflow_id="wf-1",
        image=b"fake-bytes",
        image_filename="board.png",
        metadata={"line": "SMT-3"},
    )

    assert sample.workflow_id == "wf-1"
    assert sample.image == b"fake-bytes"
    assert sample.image_filename == "board.png"
    assert sample.metadata == {"line": "SMT-3"}
    assert sample.reference_image is None


def test_prepare_sample_defaults_metadata_to_empty_dict() -> None:
    sample = prepare_sample(workflow_id="wf-2", image=b"x", image_filename="board.png")
    assert sample.metadata == {}
