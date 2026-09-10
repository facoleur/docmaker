"""extract : un appel LLM par fragment -> faits typés + provenance.

Le modèle ne renvoie QUE des faits présents dans le fragment ; la provenance
(SourceRef) est rattachée par le code, pas par le modèle.
"""

from __future__ import annotations

import logging

from ..config import Settings
from ..llm import LLM
from ..models import ChunkSet, Facts, FactSet, NoteFacts, RelationFacts, SourceRef, TableFacts

log = logging.getLogger(__name__)

_SYSTEM = (
    "Tu extrais un modèle de données depuis un extrait de documentation technique (domaine "
    "bancaire). Tu n'utilises QUE les informations présentes dans l'extrait : aucune table, "
    "colonne, type ou relation inventés. Si l'extrait ne décrit pas de schéma, renvoie des "
    "listes vides. En plus des tables et relations, mets dans `notes` tout fait utile qui "
    "n'entre pas dans le modèle (règle métier, cycle de vie, contrainte, rétention, "
    "dépréciation, glossaire, volumétrie, exemple) : une phrase par note, `table` = nom de la "
    "table concernée ou vide si transverse, `topic` = mot-clé court. Toujours UNIQUEMENT ce "
    "qui est écrit dans l'extrait."
)


def run(settings: Settings) -> None:
    chunkset = ChunkSet.model_validate_json((settings.build_dir / "chunks.json").read_text("utf-8"))
    llm = LLM(settings)
    total = len(chunkset.chunks)

    tables: list[TableFacts] = []
    relations: list[RelationFacts] = []
    notes: list[NoteFacts] = []
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
        notes += [NoteFacts(**nt.model_dump(), source_refs=[ref]) for nt in fs.notes]
        log.info(
            "fragment %d/%d : +%d table(s) +%d relation(s) +%d note(s)",
            n,
            total,
            len(fs.tables),
            len(fs.relations),
            len(fs.notes),
        )

    facts = Facts(tables=tables, relations=relations, notes=notes)
    (settings.build_dir / "facts.json").write_text(
        facts.model_dump_json(indent=2), encoding="utf-8"
    )
    log.info(
        "total : %d mention(s) de table, %d de relation, %d note(s)",
        len(tables),
        len(relations),
        len(notes),
    )
