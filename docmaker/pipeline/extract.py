"""extract : un appel LLM par fragment -> faits typés + provenance.

Le modèle ne renvoie QUE des faits présents dans le fragment ; la provenance
(SourceRef) est rattachée par le code, pas par le modèle.
"""

from __future__ import annotations

import logging

from ..config import Settings
from ..llm import LLM
from ..models import ChunkSet, Facts, FactSet, RelationFacts, SourceRef, TableFacts

log = logging.getLogger(__name__)

_SYSTEM = (
    "Tu extrais un modèle de données depuis un extrait de documentation technique (domaine "
    "bancaire). Tu n'utilises QUE les informations présentes dans l'extrait : aucune table, "
    "colonne, type ou relation inventés. Si l'extrait ne décrit pas de schéma, renvoie des "
    "listes vides."
)


def run(settings: Settings) -> None:
    chunkset = ChunkSet.model_validate_json(
        (settings.build_dir / "chunks.json").read_text("utf-8")
    )
    llm = LLM(settings)
    total = len(chunkset.chunks)

    tables: list[TableFacts] = []
    relations: list[RelationFacts] = []
    for n, ch in enumerate(chunkset.chunks, 1):
        ref = SourceRef(file=ch.doc, locator=ch.heading_path)
        prompt = f"Extrait — {ch.doc} :: {ch.heading_path or '(racine)'}\n\n{ch.text}"
        try:
            fs: FactSet = llm.json(prompt, FactSet, system=_SYSTEM)
        except RuntimeError as exc:
            log.error("fragment %d/%d : extraction abandonnée (%s)", n, total, exc)
            continue
        tables += [TableFacts(**t.model_dump(), source_refs=[ref]) for t in fs.tables]
        relations += [RelationFacts(**r.model_dump(), source_refs=[ref]) for r in fs.relations]
        log.info(
            "fragment %d/%d : +%d table(s) +%d relation(s)", n, total, len(fs.tables), len(fs.relations)
        )

    facts = Facts(tables=tables, relations=relations)
    (settings.build_dir / "facts.json").write_text(facts.model_dump_json(indent=2), encoding="utf-8")
    log.info("total : %d mention(s) de table, %d de relation", len(tables), len(relations))
