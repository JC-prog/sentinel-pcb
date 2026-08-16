"""Simulation server: stands in for the AOI Camera + PLC edge (docs/ADC-Design-Doc-Team10-V2.md
§5) until real line hardware is wired up. Loops over a directory of images, posting each to the
queue API at a configurable interval, and prints the workflow it created.

Also doubles as the seed-data script for the Queue/History views' demo content:
    uv run python simulation/simulate_line.py --images-dir simulation/images/seed --once

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

# Demo-plausible board/component/recipe identity per image - the source dataset's own
# annotations don't carry this, so these are just illustrative, cycled by index.
_DEMO_BOARD_INFO = [
    {"board_id": "MB-2024-REV3", "component_id": "R47", "recipe_id": "RCP-MB2024-R3-v5"},
    {"board_id": "MB-2024-REV3", "component_id": "C12", "recipe_id": "RCP-MB2024-R3-v5"},
    {"board_id": "MB-2031-REV1", "component_id": "R12", "recipe_id": "RCP-MB2031-R1-v2"},
    {"board_id": "MB-2031-REV1", "component_id": "C08", "recipe_id": "RCP-MB2031-R1-v2"},
]


def _load_images(images_dir: Path) -> list[Path]:
    images = sorted(p for p in images_dir.iterdir() if p.suffix.lower() in SUPPORTED_SUFFIXES)
    if not images:
        raise SystemExit(
            f"No images found in {images_dir}. Seed it with sample PCB images - see "
            "simulation/images/pcb-demo.png for the one currently checked in, or "
            "simulation/images/seed/ for a small real-dataset sample."
        )
    return images


def _post_image(client: httpx.Client, base_url: str, image_path: Path, index: int) -> None:
    board_info = _DEMO_BOARD_INFO[index % len(_DEMO_BOARD_INFO)]
    with image_path.open("rb") as f:
        response = client.post(
            f"{base_url}/workflows",
            files={"image": (image_path.name, f, "image/png")},
            data={**board_info, "metadata": "{}"},
            timeout=30.0,
        )
    response.raise_for_status()
    result = response.json()
    print(
        f"[{image_path.name}] workflow_id={result['workflow_id']} "
        f"board={result['board_id']} component={result['component_id']} "
        f"status={result['status']}"
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
        for index, image_path in enumerate(sequence):
            try:
                _post_image(client, args.base_url, image_path, index)
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
