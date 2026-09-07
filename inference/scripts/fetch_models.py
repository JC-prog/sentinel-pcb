#!/usr/bin/env python
"""Download every model's ONNX file listed in models.toml into an output directory.

Run from the Dockerfile at image build time so the files are baked into the image (no runtime
dependency on Hugging Face). Set HF_TOKEN in the environment for private repos.

    python scripts/fetch_models.py --manifest models.toml --out model_store
"""

import argparse
import os
import sys
import tomllib
from pathlib import Path

from huggingface_hub import hf_hub_download

PLACEHOLDER = "REPLACE_ME"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="models.toml")
    parser.add_argument("--out", default="model_store")
    args = parser.parse_args()

    manifest = tomllib.loads(Path(args.manifest).read_text())
    models = manifest.get("models", [])
    if not models:
        print(f"no [[models]] entries in {args.manifest}", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN") or None

    problems: list[str] = []
    for entry in models:
        name = entry.get("name", "<unnamed>")
        repo_id = entry.get("repo_id", "")
        filename = entry.get("filename", "model.onnx")
        revision = entry.get("revision", "main")

        if PLACEHOLDER in repo_id or any(PLACEHOLDER in label for label in entry.get("labels", [])):
            problems.append(f"{name}: still has {PLACEHOLDER} placeholders in {args.manifest}")
            continue

        try:
            downloaded = hf_hub_download(
                repo_id=repo_id, filename=filename, revision=revision, token=token
            )
        except Exception as exc:  # noqa: BLE001 - surface any hub failure with context
            problems.append(f"{name}: {repo_id}@{revision}/{filename} - {exc}")
            continue

        dest = out_dir / f"{name}.onnx"
        dest.write_bytes(Path(downloaded).read_bytes())
        print(f"{name}: {repo_id}@{revision}/{filename} -> {dest}")

    if problems:
        print("\nfailed:", *problems, sep="\n  ", file=sys.stderr)
        return 1

    print(f"\n{len(models)} model(s) written to {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
