"""infer : clustering du graphe de jointures → entités candidates — étape 5
de docs/poc-qualite-service.md.

En flocon, une entité métier est éclatée sur plusieurs tables. Le signal est
dans le **graphe**, jamais dans le vocabulaire des noms :

- deux tables jointes de façon fiable et exclusive (une seule arête vérifiée,
  côté « N » mandataire) forment un couple satellite → racine ;
- une table dont la clé est unique et référencée par ≥ 2 autres tables du
  même périmètre est une **dimension** ;
- une table au grain fin, avec des colonnes numériques additives, est un
  **fait**.

Le LLM **nomme**, il ne construit pas (couche-semantique.md §3) : `cluster()`
est pure et ne fait aucun appel réseau ; `name_candidates()` est la seule
fonction qui parle au modèle.

Sorties : `build/entities.json` (candidats + preuves) et `semantic/model.yaml`
(nommé, fusionné avec les entités déjà revues — voir `semantic/model.py`).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from pydantic import BaseModel

from ..config import Settings
from ..llm import LLM
from ..models import (
    Catalog,
    CatalogTable,
    EntityCandidate,
    EntityCandidates,
    JoinCandidate,
    Joins,
    PriorityReport,
    Profile,
)
from .model import Entity, SemanticModel, save_model

log = logging.getLogger(__name__)

_ADDITIVE_PREFIXES = ("MT_", "NB_", "QT_")  # montant, nombre, quantité — §2.6


def run(settings: Settings) -> None:
    build = settings.build_dir
    paths = {n: build / f"{n}.json" for n in ("catalog", "priority", "joins", "profile")}
    missing = [n for n, p in paths.items() if not p.exists()]
    if missing:
        raise RuntimeError(
            f"artefact(s) manquant(s) : {missing} — lancer les étapes correspondantes"
        )

    catalog = Catalog.model_validate_json(paths["catalog"].read_text(encoding="utf-8"))
    priority = PriorityReport.model_validate_json(paths["priority"].read_text(encoding="utf-8"))
    joins = Joins.model_validate_json(paths["joins"].read_text(encoding="utf-8"))
    profile = Profile.model_validate_json(paths["profile"].read_text(encoding="utf-8"))
    scope = set(priority.top(settings.oracle.top_n))

    candidates = cluster(catalog, joins, profile, scope)
    result = EntityCandidates(generated_at=datetime.now(), candidates=candidates)
    (build / "entities.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")

    model = name_candidates(candidates, catalog, LLM(settings))
    out_path = settings.build_dir.parent / "semantic" / "model.yaml"
    final = save_model(model, out_path)
    log.info(
        "inférence sémantique : %d table(s) → %d entité(s) (%d protégée(s) par une revue) → %s",
        len({t.fqn for t in catalog.tables} & scope),
        len(final.entities),
        sum(e.reviewed for e in final.entities.values()),
        out_path,
    )


# --------------------------------------------------------------------------- #
# Clustering — pure                                                           #
# --------------------------------------------------------------------------- #
def cluster(
    catalog: Catalog, joins: Joins, profile: Profile, scope: set[str]
) -> list[EntityCandidate]:
    tables = {t.fqn: t for t in catalog.tables if t.fqn in scope}
    grain_by_fqn = {g.table: g for g in profile.grain}

    verified = [c for c in joins.candidates if c.included and _tables(c) <= scope]
    graph = _adjacency(verified)

    roles: dict[str, str] = {}
    for fqn, table in tables.items():
        if _is_dimension(fqn, verified, grain_by_fqn):
            roles[fqn] = "dimension"
        elif _is_fact(table, grain_by_fqn.get(fqn)):
            roles[fqn] = "fact"

    satellite_of: dict[str, str] = {}
    for fqn in tables:
        if fqn in roles:
            continue
        neighbors = graph.get(fqn, set())
        if len(neighbors) == 1:
            (other,) = neighbors
            satellite_of[fqn] = other

    # Résolution transitive : une chaîne de satellites remonte jusqu'à une racine connue.
    resolved: dict[str, str] = {}
    for fqn in satellite_of:
        seen: set[str] = set()
        cur = fqn
        while cur in satellite_of and cur not in seen:
            seen.add(cur)
            cur = satellite_of[cur]
        resolved[fqn] = cur

    # Tout ce qui n'est ni racine classée ni satellite résolu vers une racine
    # devient sa propre racine de repli ("objet") — dégradation explicite
    # plutôt qu'une entité par table silencieuse (voir la table des risques).
    for fqn in tables:
        if fqn not in roles and resolved.get(fqn, fqn) not in roles and fqn not in satellite_of:
            roles[fqn] = "objet"
    for fqn, target in resolved.items():
        if target not in roles:
            roles[target] = "objet"

    satellites_by_root: dict[str, list[str]] = defaultdict(list)
    for fqn, target in resolved.items():
        if target != fqn:
            satellites_by_root[target].append(fqn)

    candidates = []
    for fqn, role in roles.items():
        g = grain_by_fqn.get(fqn)
        satellites = sorted(satellites_by_root.get(fqn, []))
        evidence = _evidence(fqn, satellites, role, verified, g)
        candidates.append(
            EntityCandidate(
                root=fqn,
                role=role,
                satellites=satellites,
                key=", ".join(g.key_columns) if g else "",
                is_grain=g.is_grain if g else None,
                confidence=_confidence(role, satellites, g, evidence),
                evidence=evidence,
            )
        )
    return sorted(candidates, key=lambda c: c.root)


def _tables(c: JoinCandidate) -> set[str]:
    return {c.left.rsplit(".", 1)[0], c.right.rsplit(".", 1)[0]}


def _adjacency(verified: list[JoinCandidate]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for c in verified:
        left, right = c.left.rsplit(".", 1)[0], c.right.rsplit(".", 1)[0]
        if left != right:
            graph[left].add(right)
            graph[right].add(left)
    return graph


def _is_dimension(fqn: str, verified: list[JoinCandidate], grain_by_fqn) -> bool:
    g = grain_by_fqn.get(fqn)
    if not g or not g.is_grain:
        return False
    def _references_fqn_as_unique_target(c: JoinCandidate) -> bool:
        left_table, right_table = c.left.rsplit(".", 1)[0], c.right.rsplit(".", 1)[0]
        if right_table == fqn and c.right_unique and left_table != fqn:
            return True
        return bool(left_table == fqn and c.left_unique and right_table != fqn)

    referenced_by = sum(1 for c in verified if _references_fqn_as_unique_target(c))
    return referenced_by >= 2


def _is_fact(table: CatalogTable, grain) -> bool:
    has_additive = any(
        c.data_type.upper().startswith("NUMBER")
        and any(c.name.upper().startswith(p) for p in _ADDITIVE_PREFIXES)
        for c in table.columns
    )
    return bool(grain and grain.is_grain and has_additive)


def _evidence(fqn, satellites, role, verified, grain) -> list[str]:
    ev = [f"grain:{grain.evidence}" if grain else "grain:aucune"]
    n_edges = sum(1 for c in verified if fqn in _tables(c))
    ev.append(f"jointures_verifiees:{n_edges}")
    if satellites:
        ev.append(f"satellites:{len(satellites)}")
    ev.append(f"role:{role}")
    return ev


def _confidence(role: str, satellites: list[str], grain, evidence: list[str]) -> float:
    score = 0.4 if role != "objet" else 0.2
    if grain and grain.evidence == "declared_pk":
        score += 0.2
    elif grain and grain.evidence == "unique_index":
        score += 0.1
    if grain and grain.is_grain:
        score += 0.2
    score += min(len(satellites) * 0.05, 0.2)
    return round(min(score, 1.0), 2)


# --------------------------------------------------------------------------- #
# Nommage LLM — seule partie non pure de ce module                           #
# --------------------------------------------------------------------------- #
class _Naming(BaseModel):
    slug: str  # identifiant court, snake_case, pour la clé du YAML
    grain: str  # une phrase : "un incident déclaré", etc.


def name_candidates(candidates: list[EntityCandidate], catalog: Catalog, llm: LLM) -> SemanticModel:
    entities: dict[str, Entity] = {}
    columns_by_fqn = {t.fqn: t for t in catalog.tables}

    for c in candidates:
        table = columns_by_fqn.get(c.root)
        naming = _propose_name(c, table, llm)
        slug = naming.slug
        suffix = 2
        while slug in entities:  # deux racines nommées pareil par le LLM
            slug = f"{naming.slug}_{suffix}"
            suffix += 1
        entities[slug] = Entity(
            root=c.root,
            satellites=c.satellites,
            grain=naming.grain,
            key=c.key,
            confidence=c.confidence,
            evidence=c.evidence,
        )
    return SemanticModel(entities=entities)


def _propose_name(candidate: EntityCandidate, table: CatalogTable | None, llm: LLM) -> _Naming:
    columns = ", ".join(col.name for col in (table.columns if table else []))[:400]
    prompt = (
        f"Table racine : {candidate.root} (rôle déduit : {candidate.role}).\n"
        f"Commentaire Oracle : {table.comment if table else '(aucun)'}\n"
        f"Colonnes : {columns}\n"
        f"Tables satellites : {', '.join(candidate.satellites) or '(aucune)'}\n\n"
        "Propose un identifiant court en snake_case (français, métier, pas le nom "
        "technique de la table) pour cette entité, et une phrase décrivant sa maille "
        "(« un incident déclaré », « un client », etc.). Si les preuves sont "
        "insuffisantes pour nommer l'entité avec confiance, réponds "
        '{"slug": "a_valider", "grain": "preuve insuffisante"}.'
    )
    try:
        naming = llm.json(prompt, _Naming)
        return naming  # type: ignore[return-value]
    except Exception:  # noqa: BLE001 — un échec de nommage ne bloque pas le pipeline
        log.warning("nommage LLM indisponible pour %s — repli sur le nom technique", candidate.root)
        return _Naming(slug=candidate.root.split(".")[-1].lower(), grain="")
