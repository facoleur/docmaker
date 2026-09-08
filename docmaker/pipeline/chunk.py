"""chunk : Markdown -> fragments alignés sur les titres (avec hard-split si trop long).

Pas de tokenizer ni de HybridChunker docling en v1 : découpe purement structurelle.
"""

import logging
import re
from collections.abc import Iterator

from ..config import ChunkConfig, Settings
from ..models import Chunk, ChunkSet, Manifest

log = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def run(settings: Settings) -> None:
    manifest = Manifest.model_validate_json(
        (settings.build_dir / "manifest.json").read_text("utf-8")
    )
    chunks: list[Chunk] = []
    n_docs = 0
    for doc in manifest.docs:
        if doc.status != "converted" or not doc.md_path:
            continue
        n_docs += 1
        text = (settings.build_dir / doc.md_path).read_text("utf-8")
        for heading_path, body, start in _sections(text):
            chunks.extend(_split(doc.path, heading_path, body, start, settings.chunk))

    (settings.build_dir / "chunks.json").write_text(
        ChunkSet(chunks=chunks).model_dump_json(indent=2), encoding="utf-8"
    )
    log.info("%d fragment(s) depuis %d document(s)", len(chunks), n_docs)


def _sections(text: str) -> Iterator[tuple[str, str, int]]:
    """Découpe sur les titres Markdown. Rend (chemin_de_titre, corps, offset_début)."""
    stack: list[str] = []
    path = ""
    buf: list[str] = []
    offset = start = 0
    for line in text.splitlines(keepends=True):
        m = _HEADING_RE.match(line.rstrip("\n"))
        if m:
            if buf:
                yield path, "".join(buf), start
            level = len(m.group(1))
            stack[:] = stack[: level - 1] + [m.group(2).strip()]
            path = " / ".join(stack)
            buf = []
            start = offset + len(line)
        else:
            buf.append(line)
        offset += len(line)
    if buf:
        yield path, "".join(buf), start


def _split(doc: str, heading_path: str, body: str, start: int, cfg: ChunkConfig) -> list[Chunk]:
    body = body.strip("\n")
    if not body.strip():  # section vide (lignes blanches entre deux titres)
        return []
    if len(body) <= cfg.max_chars:
        return [
            Chunk(doc=doc, heading_path=heading_path, text=body, start=start, end=start + len(body))
        ]

    out: list[Chunk] = []
    step = max(1, cfg.max_chars - cfg.overlap_chars)
    for i in range(0, len(body), step):
        piece = body[i : i + cfg.max_chars]
        out.append(
            Chunk(
                doc=doc,
                heading_path=heading_path,
                text=piece,
                start=start + i,
                end=start + i + len(piece),
            )
        )
    return out
