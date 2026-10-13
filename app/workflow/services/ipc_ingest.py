"""Fills the collection Agent 2's precedent search reads (`pcb_standards_precedents`) from files you
supply - IPC standard text, an internal acceptance guide, worked defect write-ups.

Upstream's reader (src/data/qdrant_store.py) searched that collection but nothing in the project ever
created it, so it was always empty. This is the missing half. It ships no content: the standards are
licensed documents, so what goes in is up to whoever runs it.

    uv run python -m app.workflow.services.ipc_ingest path/to/file_or_dir [--replace]

Reads .txt, .md and .pdf (a PDF is read page by page, so a hit can cite its page). Chunks are about
1000 characters with some overlap. Ids are derived from file, page and chunk number, so running it
again on the same files changes nothing; `--replace` first drops what a file contributed before,
which is what you want after editing it.
"""

import argparse
import importlib
import logging
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from qdrant_client import models

logger = logging.getLogger("ipc-ingest")

CHUNK_CHARS = 1000
OVERLAP_CHARS = 150
EMBED_BATCH = 64
SUFFIXES = {".txt", ".md", ".pdf"}


def chunk_text(text: str, *, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Paragraph-aware chunks of about `size` characters, each starting `overlap` characters before
    the previous one ended so a clause split across a boundary is still whole in one chunk."""

    text = " ".join(text.split()) if "\n\n" not in text else text.strip()
    paragraphs = [" ".join(p.split()) for p in text.split("\n\n") if p.strip()] or [text]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        while len(paragraph) > size:  # one paragraph longer than a chunk: cut it
            cut = paragraph.rfind(" ", 0, size) or size
            cut = size if cut <= 0 else cut
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:cut])
            paragraph = paragraph[max(cut - overlap, 1):]
        if current and len(current) + 1 + len(paragraph) > size:
            chunks.append(current)
            current = (current[-overlap:] + " " + paragraph).strip()
        else:
            current = f"{current} {paragraph}".strip()
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def _pages(path: Path) -> Iterator[tuple[int | None, str]]:
    """(page, text) pairs of a file; page is None for a format that has none."""

    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        for number, page in enumerate(PdfReader(str(path)).pages, start=1):
            yield number, page.extract_text() or ""
    else:
        yield None, path.read_text(encoding="utf-8", errors="replace")


def discover(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix.lower() in SUFFIXES else []
    return sorted(p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)


def build_records(path: Path) -> list[dict[str, Any]]:
    """Every chunk of one file with the payload the reader returns (text, source_file, page)."""

    records: list[dict[str, Any]] = []
    for page, text in _pages(path):
        for index, chunk in enumerate(chunk_text(text)):
            key = f"{path.name}:{page}:{index}"
            records.append(
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                    "payload": {
                        "text": chunk,
                        "source_file": path.name,
                        "page": page,
                        "chunk": index,
                    },
                }
            )
    return records


def ingest(
    target: Path, *, replace: bool = False, client: Any | None = None, embed: Any | None = None
) -> dict[str, int]:
    """Embeds and stores every supported file under `target`. Returns {file name: chunks stored}.
    `client` and `embed` are for tests; by default the app's Qdrant and OpenAI embeddings."""

    store = importlib.import_module("app.workflow.src.data.qdrant_store")
    qdrant = client or store.get_qdrant_client()
    embed_fn = embed or store.embed_texts

    files = discover(target)
    if not files:
        raise ValueError(f"no .txt, .md or .pdf files at {target}")

    if not qdrant.collection_exists(store.COLLECTION_NAME):
        qdrant.create_collection(
            collection_name=store.COLLECTION_NAME,
            vectors_config=models.VectorParams(size=store.VECTOR_DIM, distance=models.Distance.COSINE),
        )

    stored: dict[str, int] = {}
    for path in files:
        records = build_records(path)
        if replace:
            qdrant.delete(
                collection_name=store.COLLECTION_NAME,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="source_file", match=models.MatchValue(value=path.name)
                            )
                        ]
                    )
                ),
            )
        for start in range(0, len(records), EMBED_BATCH):
            batch = records[start : start + EMBED_BATCH]
            vectors = embed_fn([r["payload"]["text"] for r in batch])
            qdrant.upsert(
                collection_name=store.COLLECTION_NAME,
                points=[
                    models.PointStruct(id=r["id"], vector=vec, payload=r["payload"])
                    for r, vec in zip(batch, vectors, strict=True)
                ],
            )
        stored[path.name] = len(records)
        logger.info("%s: %d chunk(s)", path.name, len(records))
    return stored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index IPC standard text for Agent 2's precedents.")
    parser.add_argument("path", type=Path, help="a .txt/.md/.pdf file, or a directory of them")
    parser.add_argument(
        "--replace", action="store_true", help="drop what each file contributed before re-adding it"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        stored = ingest(args.path, replace=args.replace)
    except (ValueError, RuntimeError) as exc:
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"indexed {sum(stored.values())} chunk(s) from {len(stored)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
