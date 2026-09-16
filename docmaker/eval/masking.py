"""masking : les trois mesures d'auto-évaluation disponibles sans questions
métier connues — docs/poc-qualite-service.md, § « Le problème d'évaluation ».

1. **Masquage des commentaires existants** — la mesure la plus importante du
   POC : les colonnes qui portent déjà un `COMMENT ON` sont un jeu de test
   gratuit, sur du réel.
2. **Masquage des FK déclarées** — retire une FK connue de l'entrée, vérifie
   que le lineage et/ou le rapprochement par nom la retrouvent comme candidate,
   et que le test d'inclusion confirme qu'elle tient.
3. **Auto-cohérence** — réannote une colonne à partir de sous-ensembles de
   preuves différents ; une description qui change du tout au tout signale une
   inférence fragile, à router en revue prioritaire.

Toutes les fonctions de comparaison sont **pures** : aucune dépendance à un
modèle d'embeddings (écartée par principe au stade actuel, voir
`docs/poc-qualite-service.md` « Écarté »). La similarité texte utilise
`difflib.SequenceMatcher` (stdlib) — un ordre de grandeur, pas une mesure
sémantique fine.

`annotate` est injecté partout (jamais un import direct du LLM dans la
logique de comparaison) : c'est ce qui rend ce module testable sans modèle.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from difflib import SequenceMatcher

from pydantic import BaseModel, Field

from ..config import Settings
from ..llm import LLM
from ..models import Catalog, Joins, Lineage, Profile
from ..pipeline.joins import declared_fk_pairs, lineage_pairs, name_match_pairs

log = logging.getLogger(__name__)

_REFUSAL_MARKERS = (
    "preuve insuffisante",
    "je ne sais pas",
    "impossible à déterminer",
    "indéterminable",
)

Annotate = Callable[[str, str], str]  # (column_fqn, evidence) -> description proposée


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def is_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def build_evidence(
    column_fqn: str,
    catalog: Catalog,
    lineage: Lineage,
    profile: Profile,
    *,
    include_lineage: bool = True,
    include_profile: bool = True,
    include_tokens: bool = True,
) -> str:
    """Assemble le contexte d'annotation d'une colonne — jamais son propre
    commentaire (c'est justement ce que la mesure 1 masque).
    """
    parts = [f"Colonne : {column_fqn}"]
    column_name = column_fqn.rsplit(".", 1)[-1]
    if include_tokens:
        tokens = [t for t in column_name.split("_") if t]
        if tokens:
            parts.append(f"Tokens du nom : {', '.join(tokens)}")
    if include_lineage:
        exprs = [c.expression for c in lineage.columns if column_fqn in (c.target, *c.sources)]
        if exprs:
            lines = "\n".join(f"  - {e}" for e in exprs[:5])
            parts.append(f"Expressions de lineage observées :\n{lines}")
    if include_profile:
        vp = next((v for v in profile.values if v.column == column_fqn), None)
        if vp and vp.top_values:
            values = ", ".join(f"{v.value} ({v.frequency:.0%})" for v in vp.top_values[:8])
            parts.append(f"Valeurs observées ({vp.source}) : {values}")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Mesure 1 — masquage des commentaires                                       #
# --------------------------------------------------------------------------- #
class MaskedColumn(BaseModel):
    column: str
    original: str
    predicted: str
    similarity: float
    refused: bool


class CommentMaskingReport(BaseModel):
    generated_at: datetime
    n_sampled: int
    n_commented_total: int
    refusal_rate: float
    accuracy: float  # part des non-refus avec similarité >= seuil
    mean_similarity: float
    items: list[MaskedColumn] = Field(default_factory=list)


def commented_columns(catalog: Catalog) -> list[tuple[str, str]]:
    """(column_fqn, commentaire) pour toute colonne de table portant un `COMMENT ON`."""
    return [
        (f"{t.fqn}.{c.name}", c.comment)
        for t in catalog.tables
        for c in t.columns
        if c.comment.strip()
    ]


def mask_comment_eval(
    catalog: Catalog,
    lineage: Lineage,
    profile: Profile,
    annotate: Annotate,
    *,
    sample_size: int,
    similarity_threshold: float,
) -> CommentMaskingReport:
    """Pure une fois `annotate` fourni : aucun I/O propre à ce module."""
    commented = commented_columns(catalog)
    step = max(1, len(commented) // sample_size) if commented else 1
    sample = commented[::step][:sample_size]

    items = []
    for fqn, original in sample:
        evidence = build_evidence(fqn, catalog, lineage, profile)
        predicted = annotate(fqn, evidence)
        items.append(
            MaskedColumn(
                column=fqn,
                original=original,
                predicted=predicted,
                similarity=round(similarity(original, predicted), 4),
                refused=is_refusal(predicted),
            )
        )

    scored = [i for i in items if not i.refused]
    n_correct = sum(1 for i in scored if i.similarity >= similarity_threshold)
    accuracy = n_correct / len(scored) if scored else 0.0
    mean_sim = sum(i.similarity for i in items) / len(items) if items else 0.0
    return CommentMaskingReport(
        generated_at=datetime.now(),
        n_sampled=len(items),
        n_commented_total=len(commented),
        refusal_rate=round(sum(i.refused for i in items) / len(items), 4) if items else 0.0,
        accuracy=round(accuracy, 4),
        mean_similarity=round(mean_sim, 4),
        items=items,
    )


# --------------------------------------------------------------------------- #
# Mesure 2 — masquage des FK déclarées                                       #
# --------------------------------------------------------------------------- #
class FKMaskingReport(BaseModel):
    generated_at: datetime
    n_declared: int
    rediscovered_by_evidence: int  # présente dans le lineage ou le rapprochement par nom
    inclusion_confirmed: int  # le test d'inclusion tient, indépendamment de la source
    fully_rediscovered: int  # les deux à la fois — le vrai taux de récupération
    missed: list[str] = Field(default_factory=list)  # paires jamais retrouvées, à examiner


def mask_fk_eval(
    catalog: Catalog, lineage: Lineage, joins: Joins, priority_scope: set[str]
) -> FKMaskingReport:
    """Pure : ne relance aucune requête — s'appuie sur `joins.json` déjà calculé.

    Le test d'inclusion ne dépend pas de la source d'évidence : `joins.json`
    l'a déjà exécuté pour toute paire candidate, y compris les FK déclarées.
    Masquer une FK revient donc à vérifier, pour sa paire, que (a) une source
    indépendante de la déclaration (lineage ou nom) l'aurait aussi proposée
    comme candidate, et (b) l'inclusion déjà mesurée tient.
    """
    declared = {frozenset(p) for p in declared_fk_pairs(catalog)}
    alt_evidence = {frozenset(p) for p in lineage_pairs(lineage)} | {
        frozenset(p) for p in name_match_pairs(catalog, priority_scope)
    }
    inclusion_by_pair = {frozenset((c.left, c.right)): c.included for c in joins.candidates}

    rediscovered = confirmed = both = 0
    missed = []
    for pair in declared:
        has_alt = pair in alt_evidence
        holds = inclusion_by_pair.get(pair) is True
        rediscovered += has_alt
        confirmed += holds
        if has_alt and holds:
            both += 1
        else:
            missed.append(" = ".join(sorted(pair)))

    return FKMaskingReport(
        generated_at=datetime.now(),
        n_declared=len(declared),
        rediscovered_by_evidence=rediscovered,
        inclusion_confirmed=confirmed,
        fully_rediscovered=both,
        missed=sorted(missed),
    )


# --------------------------------------------------------------------------- #
# Mesure 3 — auto-cohérence                                                   #
# --------------------------------------------------------------------------- #
class ConsistencyResult(BaseModel):
    column: str
    descriptions: list[str]
    min_similarity: float
    fragile: bool  # à router en revue prioritaire


def self_consistency_eval(
    column_fqn: str,
    catalog: Catalog,
    lineage: Lineage,
    profile: Profile,
    annotate: Annotate,
    *,
    similarity_threshold: float,
) -> ConsistencyResult:
    """Réannote la colonne depuis deux sous-ensembles de preuves disjoints."""
    variants = [
        build_evidence(column_fqn, catalog, lineage, profile, include_profile=False),
        build_evidence(column_fqn, catalog, lineage, profile, include_lineage=False),
    ]
    descriptions = [annotate(column_fqn, ev) for ev in variants]
    pairs_sim = [
        similarity(descriptions[i], descriptions[j])
        for i in range(len(descriptions))
        for j in range(i + 1, len(descriptions))
    ]
    min_sim = min(pairs_sim) if pairs_sim else 1.0
    return ConsistencyResult(
        column=column_fqn,
        descriptions=descriptions,
        min_similarity=round(min_sim, 4),
        fragile=min_sim < similarity_threshold,
    )


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def _llm_annotate(llm: LLM) -> Annotate:
    def annotate(column_fqn: str, evidence: str) -> str:
        prompt = (
            f"{evidence}\n\n"
            "Propose une description factuelle et concise (une phrase) pour cette "
            "colonne, à partir uniquement des preuves ci-dessus. Si elles sont "
            "insuffisantes, réponds exactement « preuve insuffisante »."
        )
        return llm.text(prompt)

    return annotate


def run(settings: Settings) -> None:
    build = settings.build_dir
    paths = {n: build / f"{n}.json" for n in ("catalog", "lineage", "profile")}
    missing = [n for n, p in paths.items() if not p.exists()]
    if missing:
        raise RuntimeError(
            f"artefact(s) manquant(s) : {missing} — lancer les étapes correspondantes"
        )

    catalog = Catalog.model_validate_json(paths["catalog"].read_text(encoding="utf-8"))
    lineage = Lineage.model_validate_json(paths["lineage"].read_text(encoding="utf-8"))
    profile = Profile.model_validate_json(paths["profile"].read_text(encoding="utf-8"))
    annotate = _llm_annotate(LLM(settings))

    report = mask_comment_eval(
        catalog,
        lineage,
        profile,
        annotate,
        sample_size=settings.eval.mask_sample_size,
        similarity_threshold=settings.eval.similarity_threshold,
    )
    path = build / "masking_comments.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    log.info(
        "masquage commentaires : accuracy=%.0f%% refus=%.0f%% (%d échantillon(s)) → %s",
        report.accuracy * 100,
        report.refusal_rate * 100,
        report.n_sampled,
        path,
    )

    joins_path = build / "joins.json"
    priority_path = build / "priority.json"
    if joins_path.exists() and priority_path.exists():
        from ..models import PriorityReport

        joins = Joins.model_validate_json(joins_path.read_text(encoding="utf-8"))
        priority = PriorityReport.model_validate_json(priority_path.read_text(encoding="utf-8"))
        scope = set(priority.top(settings.oracle.top_n))
        fk_report = mask_fk_eval(catalog, lineage, joins, scope)
        (build / "masking_fk.json").write_text(
            fk_report.model_dump_json(indent=2), encoding="utf-8"
        )
        log.info(
            "masquage FK : %d/%d retrouvées (lineage/nom + inclusion confirmée)",
            fk_report.fully_rediscovered,
            fk_report.n_declared,
        )
    else:
        log.warning("build/joins.json ou build/priority.json absent — mesure 2 (FK) sautée")


if __name__ == "__main__":
    import logging as _logging

    from ..config import load_settings

    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run(load_settings())
