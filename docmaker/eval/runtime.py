"""runtime : le modèle non bridé, mesuré par régime — docs/poc-qualite-service.md
étape 7.

Le modèle reçoit le contexte sémantique (`semantic/prompt.py`) et fait ce que
la question demande : répondre en texte libre, générer du SQL, ou **corriger
du SQL fourni**. Aucun format n'est imposé au modèle — ce qui est imposé,
c'est de **savoir ce qu'il a fait** : chaque réponse est étiquetée par régime
et mesurée séparément (un taux global mélangeant les trois est ininterprétable).

Après *n* échecs de validation (`semantic/validate.py`), le régime s'arrête sur
une **abstention motivée** — jamais une réponse plausible mais fausse. C'est le
seul comportement non négociable de ce module ; tout le reste (prompt exact,
format de sortie intermédiaire) est ouvert.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from enum import Enum

from pydantic import BaseModel

from ..config import Settings
from ..llm import LLM
from ..models import Catalog, Joins
from ..pipeline._oracle import Db
from ..semantic.model import SemanticModel
from ..semantic.prompt import build_context
from ..semantic.validate import ValidationResult, validate_sql

log = logging.getLogger(__name__)


class Regime(str, Enum):
    free_text = "free_text"
    generated_sql = "generated_sql"
    corrected_sql = "corrected_sql"


class RuntimeResult(BaseModel):
    question: str
    regime: Regime
    answer: str  # texte libre, ou le SQL final retenu
    validation: ValidationResult | None = None
    attempts: int = 1
    abstained: bool = False
    context_tokens: int = 0


class _SqlAnswer(BaseModel):
    sql: str


def answer(
    question: str,
    regime: Regime,
    llm: LLM,
    model: SemanticModel,
    catalog: Catalog,
    joins: Joins,
    settings: Settings,
    db: Db | None = None,
    existing_sql: str | None = None,
) -> RuntimeResult:
    """Point d'entrée unique : dispatch selon `regime`. `existing_sql` requis
    pour `corrected_sql`, ignoré sinon.
    """
    ctx = build_context(question, model, joins, settings.llm.max_context_tokens)

    if regime is Regime.free_text:
        text = llm.text(f"{ctx.text}\n\nQuestion : {question}", system=_SYSTEM_PROMPT)
        return RuntimeResult(
            question=question, regime=regime, answer=text, context_tokens=ctx.n_tokens
        )

    if regime is Regime.corrected_sql:
        if not existing_sql:
            raise ValueError("`existing_sql` requis pour le régime corrected_sql")
        start_prompt = (
            f"{ctx.text}\n\nSQL à corriger pour répondre à « {question} » :\n{existing_sql}"
        )
    else:
        start_prompt = f"{ctx.text}\n\nQuestion à traduire en SQL Oracle : {question}"

    cfg = settings.validator
    prompt = start_prompt
    result: ValidationResult | None = None
    for attempt in range(1, cfg.max_attempts + 1):
        try:
            answer_model = llm.json(prompt, _SqlAnswer, system=_SQL_SYSTEM_PROMPT)
            sql = answer_model.sql  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — panne du modèle = tentative ratée, pas un crash
            log.warning("génération SQL échouée (essai %d/%d) : %s", attempt, cfg.max_attempts, exc)
            continue

        result = validate_sql(
            sql,
            catalog,
            joins,
            model=model,
            db=db,
            max_cardinality=cfg.max_cardinality,
            max_result_rows=cfg.max_result_rows,
            query_timeout_seconds=cfg.query_timeout_seconds,
        )
        if result.ok:
            return RuntimeResult(
                question=question,
                regime=regime,
                answer=sql,
                validation=result,
                attempts=attempt,
                context_tokens=ctx.n_tokens,
            )
        issues = "\n".join(f"- [{i.severity}] {i.rule} : {i.message}" for i in result.issues)
        prompt = (
            f"{start_prompt}\n\nLa proposition précédente a échoué la validation :\n{sql}\n\n"
            f"Problèmes relevés :\n{issues}\n\nCorrige le SQL en conséquence."
        )

    return RuntimeResult(
        question=question,
        regime=regime,
        answer="abstention : aucune requête valide après "
        f"{cfg.max_attempts} tentative(s)",
        validation=result,
        attempts=cfg.max_attempts,
        abstained=True,
        context_tokens=ctx.n_tokens,
    )


_SYSTEM_PROMPT = (
    "Tu es un assistant analytique sur un datamart Qualité de service. Réponds "
    "uniquement à partir du contexte fourni. Si le contexte est insuffisant, dis-le "
    "explicitement plutôt que de deviner."
)
_SQL_SYSTEM_PROMPT = (
    "Tu écris du SQL Oracle. N'utilise que les tables et colonnes du contexte fourni. "
    "Réponds en JSON avec une seule clé `sql`."
)


# --------------------------------------------------------------------------- #
# Mesure par régime — jamais un taux global (voir le docstring du module)     #
# --------------------------------------------------------------------------- #
class RegimeMetrics(BaseModel):
    n: int
    ok: int
    abstained: int
    mean_attempts: float

    @property
    def ok_rate(self) -> float:
        return round(self.ok / self.n, 4) if self.n else 0.0

    @property
    def abstention_rate(self) -> float:
        return round(self.abstained / self.n, 4) if self.n else 0.0


def summarize_by_regime(results: list[RuntimeResult]) -> dict[str, RegimeMetrics]:
    """Pure : agrège des `RuntimeResult` déjà produits, séparément par régime."""
    by_regime: dict[str, list[RuntimeResult]] = defaultdict(list)
    for r in results:
        by_regime[r.regime.value].append(r)

    summary = {}
    for regime, items in by_regime.items():
        ok = sum(1 for i in items if not i.abstained and (i.validation is None or i.validation.ok))
        summary[regime] = RegimeMetrics(
            n=len(items),
            ok=ok,
            abstained=sum(i.abstained for i in items),
            mean_attempts=round(sum(i.attempts for i in items) / len(items), 2),
        )
    return summary
