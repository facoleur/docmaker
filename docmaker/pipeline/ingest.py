"""ingest : sources hétérogènes -> Markdown (docling) + manifeste.

Les diagrammes/images sont décrits par la VLM quand on récupère les pixels ;
sinon comptés dans `unhandled_assets` (perte tracée, jamais silencieuse).
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import datetime
from pathlib import Path

from ..config import Settings
from ..llm import LLM
from ..models import Manifest, SourceDoc

log = logging.getLogger(__name__)


def run(settings: Settings) -> None:
    md_dir = settings.build_dir / "md"
    md_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = settings.build_dir / "assets"

    converter = _converter(settings)
    llm = LLM(settings) if settings.vlm.enabled else None

    paths = sorted(
        p
        for p in settings.source_dir.rglob("*")
        if p.is_file() and p.suffix.lower().lstrip(".") in settings.formats
    )
    if not paths:
        log.warning("aucun fichier %s sous %s", settings.formats, settings.source_dir)

    docs: list[SourceDoc] = []
    for path in paths:
        rel = path.relative_to(settings.source_dir).as_posix()
        fmt = path.suffix.lower().lstrip(".")
        sha = _sha256(path)
        try:
            document = converter.convert(path).document
            md = document.export_to_markdown()
            descriptions, unhandled = _describe_images(document, llm, assets_dir, rel, settings)
            if descriptions:
                md += "\n\n" + _image_section(descriptions)
            slug = _slug(rel)
            (md_dir / f"{slug}.md").write_text(md, encoding="utf-8")
            docs.append(
                SourceDoc(
                    path=rel,
                    fmt=fmt,
                    sha256=sha,
                    status="converted",
                    md_path=f"md/{slug}.md",
                    image_descriptions=len(descriptions),
                    unhandled_assets=unhandled,
                )
            )
            log.info(
                "converti %s (%d image(s) décrite(s), %d non traitée(s))",
                rel,
                len(descriptions),
                unhandled,
            )
        except Exception as exc:  # noqa: BLE001 - docling lève des types variés
            docs.append(SourceDoc(path=rel, fmt=fmt, sha256=sha, status="error", note=repr(exc)[:300]))
            log.error("échec %s : %s", rel, exc)

    manifest = Manifest(generated_at=datetime.now(), docs=docs)
    (settings.build_dir / "manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )


def _converter(settings: Settings):
    from docling.document_converter import DocumentConverter

    if not settings.vlm.enabled:
        return DocumentConverter()
    try:  # active la génération des images pour les PDF (principale source de diagrammes)
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import PdfFormatOption

        opts = PdfPipelineOptions()
        opts.generate_picture_images = True
        opts.images_scale = 2.0
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("images PDF non activées (%s) — fallback défaut", exc)
        return DocumentConverter()


def _describe_images(document, llm, assets_dir: Path, rel: str, settings: Settings):
    pictures = list(getattr(document, "pictures", []) or [])
    if not pictures:
        return [], 0
    if llm is None:
        return [], len(pictures)

    assets_dir.mkdir(parents=True, exist_ok=True)
    descriptions: list[str] = []
    unhandled = 0
    for i, pic in enumerate(pictures):
        try:
            image = pic.get_image(document)
        except Exception:  # noqa: BLE001
            image = None
        if image is None:
            unhandled += 1
            continue
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        png = buf.getvalue()
        (assets_dir / f"{_slug(rel)}-{i}.png").write_bytes(png)
        try:
            descriptions.append(llm.describe_image(png, settings.vlm.prompt))
        except Exception as exc:  # noqa: BLE001
            log.warning("VLM en échec (%s image %d) : %s", rel, i, exc)
            unhandled += 1
    return descriptions, unhandled


def _image_section(descriptions: list[str]) -> str:
    out = ["## Diagrammes (description automatique — VLM)", ""]
    for i, desc in enumerate(descriptions, 1):
        out += [f"### Figure {i}", "", desc.strip(), ""]
    return "\n".join(out)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "doc"
