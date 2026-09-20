"""Ported verbatim from orchestrator-agent/adc_agentic_project's services/dataset_preparation.py,
only the import paths changed."""

from pathlib import Path
import xml.etree.ElementTree as ET
import pandas as pd
from app.agents.orchestrator_agent.services.common import ServiceResult

REQUIRED_COLUMNS = {
    "SampleID", "Board", "Package", "Component", "InspectionDefinition",
    "Feature", "Defect", "GoldenImage", "DefectImage"
}

class DatasetPreparationService:
    """Prepare ADC samples. InspectionDefinition supports pipe-separated failed inspections, e.g. AI2|BlobDetection."""

    def prepare(self, dataset_csv: str, inspection_xml: str, image_root: str | None = None) -> ServiceResult:
        try:
            df = pd.read_csv(dataset_csv)
        except Exception as exc:
            return ServiceResult(False, "DATASET_READ_FAILED", errors=[str(exc)], recoverable=True)

        missing = sorted(REQUIRED_COLUMNS - set(df.columns))
        if missing:
            return ServiceResult(False, "DATASET_SCHEMA_INVALID",
                                 errors=[f"Missing columns: {', '.join(missing)}"], recoverable=True)

        try:
            root = ET.parse(inspection_xml).getroot()
        except Exception as exc:
            return ServiceResult(False, "XML_READ_FAILED", errors=[str(exc)], recoverable=True)

        samples, errors = [], []

        for _, row in df.iterrows():
            sample = self._base_sample(row, image_root)
            requested = self._parse_inspection_definitions(str(row["InspectionDefinition"]))
            sample["inspection_definitions"] = requested

            feature = self._find_failed_feature(
                root,
                str(row["Board"]).strip(),
                str(row["Component"]).strip(),
                str(row["Package"]).strip(),
                str(row["Feature"]).strip(),
            )

            if feature is None:
                sample["preparation_status"] = "FAILED"
                sample["preparation_error"] = "FAILED_FEATURE_NOT_FOUND"
                sample["missing_failed_inspections"] = requested
                errors.append(f'{sample["sample_id"]}: failed feature not found')
                samples.append(sample)
                continue

            matched, missing_types = self._find_failed_inspections(feature, requested)
            sample["xml_feature_status"] = feature.attrib.get("FeatureStatus")
            sample["failed_inspections"] = matched
            sample["missing_failed_inspections"] = missing_types

            if missing_types:
                sample["preparation_status"] = "FAILED"
                sample["preparation_error"] = "FAILED_INSPECTION_NOT_FOUND"
                errors.append(
                    f'{sample["sample_id"]}: missing failed inspection(s): {", ".join(missing_types)}'
                )
            else:
                sample["preparation_status"] = "READY"

            samples.append(sample)

        ready = sum(s["preparation_status"] == "READY" for s in samples)
        return ServiceResult(
            success=ready > 0,
            status="PREPARATION_COMPLETED" if ready == len(samples) else "PREPARATION_PARTIAL",
            message=f"Prepared {ready}/{len(samples)} samples.",
            data={"samples": samples},
            metrics={"total_samples": len(samples), "ready_samples": ready,
                     "failed_samples": len(samples) - ready},
            errors=errors,
            recoverable=ready < len(samples),
            next_action="dataset_verification" if ready else None
        )

    def _base_sample(self, row, image_root):
        return {
            "sample_id": str(row["SampleID"]),
            "board": str(row["Board"]),
            "package": str(row["Package"]),
            "component": str(row["Component"]),
            "inspection_definition_raw": str(row["InspectionDefinition"]),
            "inspection_definitions": [],
            "source_feature": str(row["Feature"]),
            "machine_defect": str(row["Defect"]),
            "timestamp": str(row.get("Timestamp", "")),
            "golden_image": self._resolve_image(str(row["GoldenImage"]), image_root),
            "defect_image": self._resolve_image(str(row["DefectImage"]), image_root),
            "failed_inspections": {},
            "missing_failed_inspections": [],
        }

    @staticmethod
    def _parse_inspection_definitions(raw_value: str):
        result, seen = [], set()
        for value in raw_value.split("|"):
            name = value.strip()
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                result.append(name)
        return result

    @staticmethod
    def _resolve_image(raw_path: str, image_root: str | None):
        """
        Resolve image path from dataset.csv.

        Example:
          CSV path:
            C:\\Users\\zeyu.wang\\Desktop\\usi\\35-...\\Text\\Golden\\image.jpg
          Image Folder:
            D:\\zwang\\Project\\adc_agentic_project\\sample_data
          Resolved:
            D:\\zwang\\Project\\adc_agentic_project\\sample_data\\35-...\\Text\\Golden\\image.jpg

        Resolution order:
          1. Existing original CSV path.
          2. image_root + relative path below the original "usi" folder.
          3. image_root + filename only (fallback).
        """
        if not raw_path:
            return raw_path

        original = Path(raw_path)
        if original.exists():
            return str(original)

        if not image_root:
            return raw_path

        new_root = Path(image_root)

        # dataset.csv contains ordinary Windows backslashes.
        normalized = raw_path.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part]
        basename = parts[-1] if parts else Path(raw_path).name

        usi_index = None
        for index, part in enumerate(parts):
            if part.lower() == "usi":
                usi_index = index
                break

        mapped = None
        if usi_index is not None and usi_index + 1 < len(parts):
            relative_parts = parts[usi_index + 1:]
            mapped = new_root.joinpath(*relative_parts)
            if mapped.exists():
                return str(mapped)

        fallback = new_root / basename
        if fallback.exists():
            return str(fallback)

        # Return the expected mapped path for a useful verifier error message.
        return str(mapped if mapped is not None else fallback)

    @staticmethod
    def _find_failed_feature(root, board_name, component_name, package, feature_name):
        for board in root.iter("Board"):
            if board.attrib.get("Name", "").strip() != board_name:
                continue
            for component in board.findall("Component"):
                if component.attrib.get("Name", "").strip() != component_name:
                    continue
                xml_pkg = component.attrib.get("Package", "").strip()
                part = component.attrib.get("PartNumber", "").strip()
                if package and xml_pkg and package not in (xml_pkg, part) and not xml_pkg.startswith(package):
                    continue
                for feature in component.findall("Feature"):
                    if feature.attrib.get("Identifier", "").strip() == feature_name and \
                            feature.attrib.get("FeatureStatus", "").strip().lower() == "failed":
                        return feature
        return None

    def _find_failed_inspections(self, feature, requested_types):
        requested = {x.lower(): x for x in requested_types}
        matched = {}
        feature_result = feature.find("FeatureResult")
        if feature_result is None:
            return matched, list(requested_types)

        for inspection in feature_result.findall("Inspection"):
            inspection_type = inspection.attrib.get("Type", "").strip()
            status = inspection.attrib.get("status", "").strip()
            key = inspection_type.lower()
            if key in requested and status.lower() == "failed":
                name = requested[key]
                matched[name] = {
                    "status": status,
                    "failed_criteria": self._extract_failed_criteria(inspection),
                    "measurements": self._extract_measurements(inspection),
                }

        missing = [x for x in requested_types if x not in matched]
        return matched, missing

    @staticmethod
    def _extract_failed_criteria(inspection):
        return [x.strip() for x in inspection.attrib.get("FailedInspectionCriterias", "").split(",") if x.strip()]

    @staticmethod
    def _extract_measurements(inspection):
        result = {}
        measurements = inspection.find("Measurements")
        if measurements is None:
            return result
        for child in list(measurements):
            result[child.tag] = dict(child.attrib)
        return result
