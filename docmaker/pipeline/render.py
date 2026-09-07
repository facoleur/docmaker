"""render : modèle réconcilié -> Markdown à frontmatter + chunks.jsonl RAG-ready.

Le tableau des colonnes est rendu déterministiquement (Jinja) depuis le modèle.
Seule la prose d'introduction passe par le LLM, contrainte au JSON de l'entité.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

from jinja2 import Environment, PackageLoader

from ..config import Settings
from ..llm import LLM
from ..models import DocModel, Entity

log = logging.getLogger(__name__)

_OVERVIEW_SYSTEM = (
    "Tu rédiges 2 à 4 phrases de présentation d'une table de base de données, en français, "
    "UNIQUEMENT à partir du JSON fourni. N'ajoute aucun fait absent du JSON. Un seul paragraphe, "
    "pas de liste."
)


def run(settings: Settings) -> None:
    model = DocModel.model_validate_json((settings.build_dir / "model.json").read_text("utf-8"))

    env = Environment(loader=PackageLoader("docmaker", "templates"), trim_blocks=True, lstrip_blocks=True)
    env.filters["slug"] = _slug

    out = settings.out_dir
    (out / "tables").mkdir(parents=True, exist_ok=True)
    llm = LLM(settings)
    today = date.today().isoformat()

    table_tpl = env.get_template("table_reference.md.j2")
    records: list[dict] = []
    for ent in model.entities:
        conflicts = [
            c for c in model.conflicts if c.entity == ent.name or c.entity.startswith(ent.name + ".")
        ]
        md = table_tpl.render(
            e=ent,
            overview=_overview(llm, ent),
            conflicts=conflicts,
            system=settings.system_name,
            today=today,
            model=settings.llm.model,
        )
        slug = _slug(ent.name)
        (out / "tables" / f"{slug}.md").write_text(md, encoding="utf-8")
        records.append(
            {
                "id": f"table/{slug}",
                "title": ent.name,
                "doc_type": "table_reference",
                "entities": [ent.name],
                "sources": sorted({r.file for r in ent.source_refs}),
                "text": md,
            }
        )

    index_md = env.get_template("index.md.j2").render(model=model, system=settings.system_name, today=today)
    (out / "index.md").write_text(index_md, encoding="utf-8")
    records.append(
        {
            "id": "index",
            "title": f"Index — {settings.system_name}",
            "doc_type": "index",
            "entities": [e.name for e in model.entities],
            "sources": [],
            "text": index_md,
        }
    )

    with (out / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log.info("%d fichier(s) Markdown + chunks.jsonl dans %s", len(records), out)


def _overview(llm: LLM, ent: Entity) -> str:
    try:
        return llm.text(ent.model_dump_json(indent=2), system=_OVERVIEW_SYSTEM)
    except Exception as exc:  # noqa: BLE001
        log.warning("intro non générée pour %s : %s", ent.name, exc)
        return ent.description or ""


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "entite"
