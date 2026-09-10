"""extract : un appel LLM par fragment -> faits typés + provenance.

Le modèle ne renvoie QUE des faits présents dans le fragment ; la provenance
(SourceRef) est rattachée par le code, pas par le modèle.

Écriture incrémentale (`prise-de-recul.md` §7.2) : chaque fragment extrait est
ajouté à `facts.jsonl` dès qu'il est obtenu, et non à la fin du run. Une
interruption — timeout, coupure d'endpoint, Ctrl-C — ne perd rien, et un
relancement reprend où il s'est arrêté au lieu de repayer les appels déjà faits.
Un fragment dont le texte a changé porte une clé différente : il est réextrait.
Les entrées devenues orphelines (fragment disparu) sont simplement ignorées.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from ..config import Settings
from ..llm import LLM
from ..models import (
    Chunk,
    ChunkSet,
    Facts,
    FactSet,
    NoteFacts,
    RelationFacts,
    SourceRef,
    TableFacts,
)

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
    cache_path = settings.build_dir / "facts.jsonl"
    cached = _load_cache(cache_path)
    llm = LLM(settings)
    total = len(chunkset.chunks)
    reused = failed = 0

    with cache_path.open("a", encoding="utf-8") as cache:
        for n, ch in enumerate(chunkset.chunks, 1):
            key = _chunk_key(ch)
            if key in cached:
                reused += 1
                continue
            prompt = f"Extrait — {ch.doc} :: {ch.heading_path or '(racine)'}\n\n{ch.text}"
            try:
                fs: FactSet = llm.json(prompt, FactSet, system=_SYSTEM)
            except Exception as exc:  # noqa: BLE001 — un fragment perdu ne doit pas tuer le run
                failed += 1
                log.error("fragment %d/%d : extraction abandonnée (%s)", n, total, exc)
                continue
            record = {"key": key, "facts": fs.model_dump()}
            cache.write(json.dumps(record, ensure_ascii=False) + "\n")
            cache.flush()  # le cache doit survivre à un kill -9, pas seulement à une exception
            cached[key] = record["facts"]
            log.info(
                "fragment %d/%d : +%d table(s) +%d relation(s) +%d note(s)",
                n,
                total,
                len(fs.tables),
                len(fs.relations),
                len(fs.notes),
            )

    if reused:
        log.info("%d fragment(s) repris du cache %s", reused, cache_path.name)
    if failed:
        log.warning("%d fragment(s) en échec — relancer l'étape les réessaiera", failed)

    facts = _assemble(chunkset, cached)
    (settings.build_dir / "facts.json").write_text(
        facts.model_dump_json(indent=2), encoding="utf-8"
    )
    log.info(
        "total : %d mention(s) de table, %d de relation, %d note(s)",
        len(facts.tables),
        len(facts.relations),
        len(facts.notes),
    )


def _chunk_key(ch: Chunk) -> str:
    """Identité du fragment : son contenu, pas sa position. Un texte modifié est réextrait."""
    payload = f"{ch.doc}\x00{ch.heading_path}\x00{ch.text}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _load_cache(path: Path) -> dict[str, dict]:
    """Relit `facts.jsonl`. Une ligne tronquée (kill en pleine écriture) est ignorée."""
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for lineno, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            out[rec["key"]] = rec["facts"]
        except (json.JSONDecodeError, KeyError):
            log.warning("cache %s ligne %d illisible, ignorée", path.name, lineno)
    return out


def _assemble(chunkset: ChunkSet, cached: dict[str, dict]) -> Facts:
    """Reconstruit `Facts` dans l'ordre des fragments courants, provenance rattachée ici."""
    tables: list[TableFacts] = []
    relations: list[RelationFacts] = []
    notes: list[NoteFacts] = []
    for ch in chunkset.chunks:
        raw = cached.get(_chunk_key(ch))
        if raw is None:  # fragment jamais extrait, ou en échec
            continue
        fs = FactSet.model_validate(raw)
        ref = SourceRef(file=ch.doc, locator=ch.heading_path)
        tables += [TableFacts(**t.model_dump(), source_refs=[ref]) for t in fs.tables]
        relations += [RelationFacts(**r.model_dump(), source_refs=[ref]) for r in fs.relations]
        notes += [NoteFacts(**nt.model_dump(), source_refs=[ref]) for nt in fs.notes]
    return Facts(tables=tables, relations=relations, notes=notes)
