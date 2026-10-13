from services.dataset_preparation import DatasetPreparationService


def test_image_root_preserves_path_below_usi():
    raw = (
        r"C:\Users\zeyu.wang\Desktop\usi"
        r"\35-900032-AAA-RV1\Text\Golden"
        r"\Board1_U51_Text1_35-900032-AAA-RV1_20260908_144902427_Golden.jpg"
    )

    resolved = DatasetPreparationService._resolve_image(
        raw,
        r"D:\Image",
    )

    normalized = resolved.replace("\\", "/")

    assert normalized.endswith(
        "D:/Image/35-900032-AAA-RV1/Text/Golden/"
        "Board1_U51_Text1_35-900032-AAA-RV1_20260908_144902427_Golden.jpg"
    )


def test_image_root_falls_back_to_filename_when_usi_missing():
    raw = r"C:\SomeOtherFolder\Golden\abc.jpg"

    resolved = DatasetPreparationService._resolve_image(
        raw,
        r"D:\Image",
    )

    normalized = resolved.replace("\\", "/")
    assert normalized.endswith("D:/Image/abc.jpg")
