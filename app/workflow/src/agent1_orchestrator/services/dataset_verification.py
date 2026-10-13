from pathlib import Path
from services.common import ServiceResult
from verification.image_quality import image_quality
from verification.image_alignment import estimate_translation
from verification.schema_validation import validate_pair_schema
from verification.measurement_validation import validate_measurements

class DatasetVerificationService:
    def __init__(self, warning_shift_pixels=12.0, fail_shift_pixels=35.0, alignment_enabled=True):
        self.warning_shift = warning_shift_pixels
        self.fail_shift = fail_shift_pixels
        self.alignment_enabled = alignment_enabled

    def verify(self, prepared_samples):
        results, verified = [], []

        for sample in prepared_samples:
            issues, warnings = [], []

            if sample.get("preparation_status") != "READY":
                issues.append("PREPARATION_NOT_READY")

            requested = sample.get("inspection_definitions") or []
            matched = sample.get("failed_inspections") or {}
            missing = sample.get("missing_failed_inspections") or []

            if not requested:
                issues.append("INSPECTION_DEFINITION_EMPTY")

            for name in missing:
                issues.append(f"FAILED_INSPECTION_NOT_FOUND:{name}")

            for name in requested:
                if name not in matched:
                    issue = f"FAILED_INSPECTION_NOT_FOUND:{name}"
                    if issue not in issues:
                        issues.append(issue)

            gpath = sample.get("golden_image", "")
            dpath = sample.get("defect_image", "")
            if not Path(gpath).exists():
                issues.append("GOLDEN_IMAGE_NOT_FOUND")
            if not Path(dpath).exists():
                issues.append("DEFECT_IMAGE_NOT_FOUND")

            gq = image_quality(gpath) if Path(gpath).exists() else {"readable": False}
            dq = image_quality(dpath) if Path(dpath).exists() else {"readable": False}

            if Path(gpath).exists() and not gq.get("readable"):
                issues.append("GOLDEN_IMAGE_UNREADABLE")
            if Path(dpath).exists() and not dq.get("readable"):
                issues.append("DEFECT_IMAGE_UNREADABLE")

            schema = validate_pair_schema(sample)
            if not schema["matched"]:
                warnings.append("IMAGE_FILENAME_SCHEMA_MISMATCH")

            measurement = validate_measurements(sample)
            if not measurement["valid"]:
                issues.extend(measurement["issues"])

            alignment = {"success": False, "reason": "NOT_RUN"}
            if self.alignment_enabled and gq.get("readable") and dq.get("readable"):
                alignment = estimate_translation(gpath, dpath)
                if alignment.get("success"):
                    shift = alignment["shift_pixels"]
                    if shift > self.fail_shift:
                        issues.append("IMAGE_PAIR_ALIGNMENT_FAILED")
                    elif shift > self.warning_shift:
                        warnings.append("IMAGE_PAIR_ALIGNMENT_WARNING")

            status = "PASSED" if not issues else "FAILED"
            results.append({
                "sample_id": sample.get("sample_id"),
                "status": status,
                "issues": issues,
                "warnings": warnings,
                "requested_inspections": requested,
                "matched_failed_inspections": list(matched.keys()),
                "missing_failed_inspections": missing,
                "golden_quality": gq,
                "defect_quality": dq,
                "schema": schema,
                "measurement": measurement,
                "alignment": alignment,
            })
            if status == "PASSED":
                verified.append(sample)

        return ServiceResult(
            success=len(verified) > 0,
            status="VERIFICATION_PASSED" if len(verified) == len(prepared_samples) else "VERIFICATION_PARTIAL",
            message=f"Verified {len(verified)}/{len(prepared_samples)} samples.",
            data={"verified_samples": verified, "sample_results": results},
            metrics={"total_samples": len(prepared_samples), "passed_samples": len(verified),
                     "failed_samples": len(prepared_samples) - len(verified)},
            recoverable=len(verified) < len(prepared_samples),
            next_action="model_lifecycle" if verified else None
        )
