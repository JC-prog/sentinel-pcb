"""Simulation server: stands in for the AOI Camera + PLC edge (docs/ADC-Design-Doc-Team10-V2.md
§5) until real line hardware is wired up. Loops over a directory of images, posting each to the
ingest API at a configurable interval, and prints the Orchestrator's decision.

Usage:
    uv run python simulation/simulate_line.py
    uv run python simulation/simulate_line.py --images-dir simulation/images --interval 5 --once
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from pathlib import Path

import httpx

DEFAULT_IMAGES_DIR = Path(__file__).parent / "images"
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}


def _load_images(images_dir: Path) -> list[Path]:
    images = sorted(p for p in images_dir.iterdir() if p.suffix.lower() in SUPPORTED_SUFFIXES)
    if not images:
        raise SystemExit(
            f"No images found in {images_dir}. Seed it with sample PCB images - see "
            "simulation/images/pcb-demo.png for the one currently checked in."
        )
    return images


def _post_image(client: httpx.Client, base_url: str, image_path: Path) -> None:
    with image_path.open("rb") as f:
        response = client.post(
            f"{base_url}/inspections",
            files={"image": (image_path.name, f, "image/png")},
            data={"metadata": "{}"},
            timeout=30.0,
        )
    response.raise_for_status()
    result = response.json()
    print(
        f"[{image_path.name}] workflow_id={result['workflow_id']} "
        f"decision={result['decision']} confidence={result['overall_confidence']:.3f} "
        f"-> {result['rationale']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--images-dir", type=Path, default=DEFAULT_IMAGES_DIR)
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between events.")
    parser.add_argument("--once", action="store_true", help="Send one image per file, then exit.")
    args = parser.parse_args()

    images = _load_images(args.images_dir)
    print(f"Simulating an AOI line with {len(images)} image(s) from {args.images_dir}")

    with httpx.Client() as client:
        sequence: list[Path] | itertools.cycle[Path] = images if args.once else itertools.cycle(images)
        for image_path in sequence:
            try:
                _post_image(client, args.base_url, image_path)
            except httpx.ConnectError:
                print(
                    f"Could not reach {args.base_url} - is `uv run uvicorn app.main:app` running?",
                    file=sys.stderr,
                )
                raise SystemExit(1) from None
            except httpx.HTTPStatusError as exc:
                print(f"[{image_path.name}] request failed: {exc}", file=sys.stderr)
            if args.interval > 0:
                time.sleep(args.interval)


if __name__ == "__main__":
    main()
