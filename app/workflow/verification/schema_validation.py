from pathlib import Path

def _tokens(path):
    return Path(path.replace("\\", "/")).name.split("_")

def validate_pair_schema(sample):
    g = Path(sample["golden_image"].replace("\\", "/")).name
    d = Path(sample["defect_image"].replace("\\", "/")).name
    # Strong, deterministic checks from manifest values; filenames are supporting evidence.
    checks = {}
    for key, value in [
        ("board", sample.get("board")),
        ("component", sample.get("component")),
        ("feature", sample.get("source_feature")),
    ]:
        v = str(value or "")
        checks[f"{key}_in_golden"] = v in g if v else True
        checks[f"{key}_in_defect"] = v in d if v else True
    checks["golden_label_present"] = "Golden" in g
    return {"matched": all(checks.values()), "checks": checks}
