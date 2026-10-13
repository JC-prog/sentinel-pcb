"""Fills the collection Agent 2's precedent search reads (`pcb_standards_precedents`) from files you
supply - IPC standard text, an internal acceptance guide, worked defect write-ups.

Upstream's reader (src/data/qdrant_store.py) searched that collection but nothing in the project ever
created it, so it was always empty. This is the missing half. It ships no content: the standards are
licensed documents, so what goes in is up to whoever runs it.

    uv run python -m app.workflow.services.ipc_ingest path/to/file_or_dir [--replace]

Reads .txt, .md, .pdf and .json (a PDF is read page by page, so a hit can cite its page; a JSON file
is a list of standards, one chunk each - see `_json_entries`). Chunks are about 1000 characters with
some overlap. Ids are derived from file, page and chunk number, so running it again on the same
files changes nothing; `--replace` first drops what a file contributed before, which is what you
want after editing it.

Embedding is the only slow, paid part, and a collection can be moved without redoing it:

    uv run python -m app.workflow.services.ipc_ingest export corpus.jsonl
    uv run python -m app.workflow.services.ipc_ingest import corpus.jsonl [--replace] [--force]

`export` writes every chunk with its vector; `import` puts them in another Qdrant with no embedding
call and no OpenAI key. The file starts with a header naming the embedding model and dimension, and
`import` refuses a file made with a different one (its vectors would not match new queries). The
export holds the documents' text, so keep it out of git - it is as private as the documents.

An ingest also skips a file whose content is unchanged since it was last embedded (a SHA-256 stored
with its chunks); `--force` embeds it anyway.
"""

import argparse
import hashlib
import importlib
import json
import logging
import re
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qdrant_client import models

from app.shared.config.settings import settings

logger = logging.getLogger("ipc-ingest")

CHUNK_CHARS = 1000
OVERLAP_CHARS = 150
EMBED_BATCH = 64
SUFFIXES = {".txt", ".md", ".pdf", ".json"}


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


def _json_entries(path: Path) -> list[dict[str, Any]]:
    """The standards listed in a JSON file. Accepts what actually gets sent around: the document
    wrapped in a markdown code fence (```json ... ```), CRLF line endings, a BOM, and the list either
    at the top, under `standards`, or under `ipc_standards_framework.standards`."""

    raw = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n").strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*\n", "", raw)
    raw = re.sub(r"\n```\s*$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON ({exc.msg} at line {exc.lineno})") from exc

    if isinstance(data, dict) and isinstance(data.get("ipc_standards_framework"), dict):
        data = data["ipc_standards_framework"]
    if isinstance(data, dict):
        data = data.get("standards")
    if not isinstance(data, list) or not all(isinstance(e, dict) for e in data) or not data:
        raise ValueError(f"{path.name} has no list of standards to index")
    return data


def _json_entry_text(entry: dict[str, Any]) -> str:
    head = " - ".join(str(entry[k]) for k in ("standard_id", "title") if entry.get(k))
    parts = [head] if head else []
    for label, key in (("Category", "category"), ("Role", "role")):
        if entry.get(key):
            parts.append(f"{label}: {entry[key]}")
    if entry.get("relevance"):
        parts.append(str(entry["relevance"]))
    refs = entry.get("key_references")
    if isinstance(refs, list) and refs:
        parts.append("Key references: " + "; ".join(str(r) for r in refs))
    return ". ".join(p.rstrip(".") for p in parts if p)


def _pages(path: Path) -> Iterator[tuple[int | None, str]]:
    """(page, text) pairs of a file; page is None for a format that has none (one pair per
    standard for a JSON file, so each stays its own chunk)."""

    if path.suffix.lower() == ".json":
        for entry in _json_entries(path):
            text = _json_entry_text(entry)
            if text:
                yield None, text
    elif path.suffix.lower() == ".pdf":
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
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    index = 0  # the file's own running chunk number, so entries without a page stay distinct
    for page, text in _pages(path):
        for chunk in chunk_text(text):
            key = f"{path.name}:{page}:{index}"
            records.append(
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                    "payload": {
                        "text": chunk,
                        "source_file": path.name,
                        "page": page,
                        "chunk": index,
                        "source_sha256": sha,
                    },
                }
            )
            index += 1
    return records


def _already_embedded(qdrant: Any, collection: str, name: str, sha: str, expected: int) -> bool:
    """True when `name` is already in the collection with exactly this content: every chunk it
    would produce is there, with the same SHA-256."""

    same = qdrant.count(
        collection_name=collection,
        count_filter=models.Filter(
            must=[
                models.FieldCondition(key="source_file", match=models.MatchValue(value=name)),
                models.FieldCondition(key="source_sha256", match=models.MatchValue(value=sha)),
            ]
        ),
        exact=True,
    ).count
    if same != expected:
        return False
    total = qdrant.count(
        collection_name=collection,
        count_filter=models.Filter(
            must=[models.FieldCondition(key="source_file", match=models.MatchValue(value=name))]
        ),
        exact=True,
    ).count
    return bool(total == expected)  # nothing stale left over from an older, longer version


def ingest(
    target: Path,
    *,
    replace: bool = False,
    force: bool = False,
    client: Any | None = None,
    embed: Any | None = None,
) -> dict[str, int]:
    """Embeds and stores every supported file under `target`. Returns {file name: chunks stored}
    (a file that was already embedded with identical content is counted but not embedded again,
    unless `force`). `client` and `embed` are for tests; by default the app's Qdrant and OpenAI
    embeddings."""

    store = importlib.import_module("app.workflow.src.data.qdrant_store")
    qdrant = client or store.get_qdrant_client()
    embed_fn = embed or store.embed_texts

    files = discover(target)
    if not files:
        raise ValueError(f"no .txt, .md, .pdf or .json files at {target}")

    if not qdrant.collection_exists(store.COLLECTION_NAME):
        qdrant.create_collection(
            collection_name=store.COLLECTION_NAME,
            vectors_config=models.VectorParams(size=store.VECTOR_DIM, distance=models.Distance.COSINE),
        )

    stored: dict[str, int] = {}
    for path in files:
        records = build_records(path)
        if (
            not force
            and not replace
            and records
            and _already_embedded(
                qdrant,
                store.COLLECTION_NAME,
                path.name,
                records[0]["payload"]["source_sha256"],
                len(records),
            )
        ):
            stored[path.name] = len(records)
            logger.info("%s: unchanged, %d chunk(s) already embedded", path.name, len(records))
            continue
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


FORMAT = "ipc-corpus/1"


def export_corpus(destination: Path, *, client: Any | None = None) -> int:
    """Writes the whole collection - ids, vectors, payloads - to a JSONL file. Returns the count."""

    store = importlib.import_module("app.workflow.src.data.qdrant_store")
    qdrant = client or store.get_qdrant_client()
    if not qdrant.collection_exists(store.COLLECTION_NAME):
        raise ValueError(f"the collection {store.COLLECTION_NAME!r} does not exist - nothing to export")

    points: list[Any] = []
    offset: Any = None
    while True:
        batch, offset = qdrant.scroll(
            collection_name=store.COLLECTION_NAME,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        points.extend(batch)
        if offset is None:
            break
    points.sort(key=lambda p: (str(p.payload.get("source_file")), p.payload.get("chunk") or 0))

    header = {
        "format": FORMAT,
        "collection": store.COLLECTION_NAME,
        "embedding_model": settings.openai_embedding_model,
        "dimension": store.VECTOR_DIM,
        "count": len(points),
        "exported_at": datetime.now(UTC).isoformat(),
    }
    with destination.open("w", encoding="utf-8") as out:
        out.write(json.dumps(header) + "\n")
        for point in points:
            out.write(
                json.dumps({"id": str(point.id), "vector": point.vector, "payload": point.payload})
                + "\n"
            )
    return len(points)


def import_corpus(
    source: Path, *, replace: bool = False, force: bool = False, client: Any | None = None
) -> int:
    """Loads an export into the collection without embedding anything. `replace` empties the
    collection first so it ends up exactly as the file says; otherwise points are upserted by id.
    Refuses a file made with another embedding model or dimension unless `force`."""

    store = importlib.import_module("app.workflow.src.data.qdrant_store")
    qdrant = client or store.get_qdrant_client()

    with source.open(encoding="utf-8") as handle:
        try:
            header = json.loads(handle.readline())
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source.name} is not an ipc-ingest export (bad header)") from exc
        if not isinstance(header, dict) or header.get("format") != FORMAT:
            raise ValueError(f"{source.name} is not an ipc-ingest export (format {FORMAT!r} expected)")
        if header.get("dimension") != store.VECTOR_DIM:
            raise ValueError(
                f"{source.name} has {header.get('dimension')}-dimension vectors; this app's are "
                f"{store.VECTOR_DIM}. They cannot be searched with its embeddings."
            )
        if header.get("embedding_model") != settings.openai_embedding_model and not force:
            raise ValueError(
                f"{source.name} was embedded with {header.get('embedding_model')!r} but this app "
                f"embeds queries with {settings.openai_embedding_model!r}; its vectors would not "
                "match them. Re-ingest the documents, or pass --force to load it anyway."
            )

        lines = [line for line in handle if line.strip()]

    points: list[models.PointStruct] = []
    for number, line in enumerate(lines, start=2):
        try:
            item = json.loads(line)
            vector = item["vector"]
            if len(vector) != store.VECTOR_DIM:
                raise ValueError(f"vector of {len(vector)} values")
            points.append(
                models.PointStruct(id=item["id"], vector=vector, payload=item["payload"])
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{source.name}: line {number} is not a valid point ({exc})") from exc
    if header.get("count") is not None and header["count"] != len(points):
        raise ValueError(
            f"{source.name} says {header['count']} points but holds {len(points)} - truncated?"
        )

    if replace and qdrant.collection_exists(store.COLLECTION_NAME):
        qdrant.delete_collection(store.COLLECTION_NAME)
    if not qdrant.collection_exists(store.COLLECTION_NAME):
        qdrant.create_collection(
            collection_name=store.COLLECTION_NAME,
            vectors_config=models.VectorParams(size=store.VECTOR_DIM, distance=models.Distance.COSINE),
        )
    for start in range(0, len(points), 128):
        qdrant.upsert(collection_name=store.COLLECTION_NAME, points=points[start : start + 128])
    return len(points)


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO)

    if args_in[:1] == ["export"]:
        parser = argparse.ArgumentParser(prog="ipc_ingest export")
        parser.add_argument("file", type=Path, help="where to write the JSONL export")
        args = parser.parse_args(args_in[1:])
        try:
            count = export_corpus(args.file)
        except (ValueError, OSError) as exc:
            print(f"export failed: {exc}", file=sys.stderr)
            return 1
        print(f"exported {count} chunk(s) to {args.file}")
        return 0

    if args_in[:1] == ["import"]:
        parser = argparse.ArgumentParser(prog="ipc_ingest import")
        parser.add_argument("file", type=Path, help="an export made by `ipc_ingest export`")
        parser.add_argument(
            "--replace", action="store_true", help="empty the collection first, so it matches the file"
        )
        parser.add_argument(
            "--force", action="store_true", help="load a file made with a different embedding model"
        )
        args = parser.parse_args(args_in[1:])
        try:
            count = import_corpus(args.file, replace=args.replace, force=args.force)
        except (ValueError, OSError) as exc:
            print(f"import failed: {exc}", file=sys.stderr)
            return 1
        print(f"imported {count} chunk(s) from {args.file} (no embeddings requested)")
        return 0

    parser = argparse.ArgumentParser(
        description="Index IPC standard text for Agent 2's precedents.",
        epilog="Also: `export FILE` and `import FILE [--replace] [--force]` move a collection "
        "between Qdrants without re-embedding.",
    )
    parser.add_argument(
        "path", type=Path, help="a .txt/.md/.pdf/.json file, or a directory of them"
    )
    parser.add_argument(
        "--replace", action="store_true", help="drop what each file contributed before re-adding it"
    )
    parser.add_argument(
        "--force", action="store_true", help="embed a file even if it is unchanged since last time"
    )
    args = parser.parse_args(args_in)
    try:
        stored = ingest(args.path, replace=args.replace, force=args.force)
    except (ValueError, RuntimeError) as exc:
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"indexed {sum(stored.values())} chunk(s) from {len(stored)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
