"""validate : le validateur SQL — docs/poc-qualite-service.md étape 7.

Contrainte non négociable, mais **le validateur ne bride pas le modèle** : le
modèle répond en texte libre, génère du SQL ou en corrige, sans format imposé.
C'est *après coup* que tout SQL produit doit passer les six règles, dans
l'ordre, chacune pouvant rejeter ou seulement avertir :

1. `sqlglot` parse (dialecte `oracle`) — échec ⇒ rejet, on s'arrête là.
2. Toute table et colonne citée existe dans `catalog.json` — sinon rejet.
   Le plus gros rendement pour le plus petit code (voir le critère de l'étape).
3. Toute jointure utilisée est une arête vérifiée de `joins.json` — sinon
   avertissement (elle peut être correcte mais jamais testée : la marge de
   confiance est signalée, pas cachée).
4. Filtres par défaut des entités touchées présents — sinon avertissement.
5. `EXPLAIN PLAN` : cardinalité estimée aberrante ⇒ rejet.
6. Exécution : lecture seule, `FETCH FIRST n ROWS ONLY`, timeout serveur.

Rejet = `ok=False`. Un avertissement seul n'empêche pas `ok=True` : c'est à
`docmaker/eval/runtime.py` de décider, après *n* échecs, de l'abstention
motivée plutôt qu'une réponse plausible.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from ..models import Catalog, Joins
from ..pipeline._oracle import Db
from ..pipeline.lineage import _alias_map, _resolve_column
from .model import SemanticModel

log = logging.getLogger(__name__)

Severity = Literal["error", "warning"]


class ValidationIssue(BaseModel):
    rule: str
    severity: Severity
    message: str


class ValidationResult(BaseModel):
    sql: str
    ok: bool
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def validate_sql(
    sql: str,
    catalog: Catalog,
    joins: Joins,
    model: SemanticModel | None = None,
    db: Db | None = None,
    *,
    max_cardinality: int = 5_000_000,
    max_result_rows: int = 1000,
    query_timeout_seconds: float = 30,
) -> ValidationResult:
    import sqlglot
    from sqlglot import exp

    issues: list[ValidationIssue] = []

    # 1. Parsing --------------------------------------------------------------
    try:
        tree = sqlglot.parse_one(sql, dialect="oracle")
    except Exception as exc:  # noqa: BLE001 — mesuré, pas une exception applicative
        return ValidationResult(
            sql=sql,
            ok=False,
            issues=[ValidationIssue(rule="parse", severity="error", message=str(exc)[:300])],
        )

    default_owner = catalog.owners[0] if catalog.owners else ""
    known_fqns = catalog.all_table_fqns()
    alias_map = _alias_map(tree, exp, catalog, known_fqns, default_owner)

    # 2. Vocabulaire fermé ------------------------------------------------------
    for fqn in set(alias_map.values()):
        if fqn not in known_fqns:
            issues.append(
                ValidationIssue(
                    rule="table_inconnue",
                    severity="error",
                    message=f"table absente du catalogue : {fqn}",
                )
            )
    for col in tree.find_all(exp.Column):
        resolved = _resolve_column(col, alias_map, exp)
        if resolved is None:
            issues.append(
                ValidationIssue(
                    rule="colonne_ambigue",
                    severity="warning",
                    message=f"colonne non qualifiée, vérification impossible : {col.name}",
                )
            )
            continue
        table_fqn = resolved.rsplit(".", 1)[0]
        if table_fqn in known_fqns and not catalog.has_column(table_fqn, col.name):
            issues.append(
                ValidationIssue(
                    rule="colonne_inconnue",
                    severity="error",
                    message=f"colonne absente : {resolved}",
                )
            )

    if any(i.rule in ("table_inconnue", "colonne_inconnue") for i in issues):
        return ValidationResult(sql=sql, ok=False, issues=issues)

    # 3. Jointures vérifiées ----------------------------------------------------
    verified = joins.verified_edges()
    for join in tree.find_all(exp.Join):
        for eq in join.find_all(exp.EQ):
            left = _resolve_column(eq.left, alias_map, exp)
            right = _resolve_column(eq.right, alias_map, exp)
            if left and right and frozenset((left, right)) not in verified:
                issues.append(
                    ValidationIssue(
                        rule="jointure_non_verifiee",
                        severity="warning",
                        message=f"jointure jamais confirmée par inclusion : {left} = {right}",
                    )
                )

    # 4. Filtres par défaut -------------------------------------------------
    if model is not None:
        touched = set(alias_map.values())
        sql_lower = " ".join(sql.lower().split())
        for entity in model.entities.values():
            if not ({entity.root, *entity.satellites} & touched):
                continue
            for filt in entity.default_filters:
                if " ".join(filt.lower().split()) not in sql_lower:
                    issues.append(
                        ValidationIssue(
                            rule="filtre_par_defaut_absent",
                            severity="warning",
                            message=f"filtre par défaut de {entity.root} absent : {filt}",
                        )
                    )

    # 5. EXPLAIN PLAN ---------------------------------------------------------
    if db is not None:
        cardinality = explain_cardinality(db, sql)
        if cardinality is not None and cardinality > max_cardinality:
            issues.append(
                ValidationIssue(
                    rule="cardinalite_aberrante",
                    severity="error",
                    message=f"cardinalité estimée {cardinality} > seuil {max_cardinality}",
                )
            )
        elif cardinality is None:
            issues.append(
                ValidationIssue(
                    rule="explain_indisponible",
                    severity="warning",
                    message="EXPLAIN PLAN inexploitable (PLAN_TABLE absente ou inaccessible)",
                )
            )
    else:
        issues.append(
            ValidationIssue(
                rule="explain_non_execute", severity="warning", message="pas de connexion fournie"
            )
        )

    if any(i.rule == "cardinalite_aberrante" for i in issues):
        return ValidationResult(sql=sql, ok=False, issues=issues)

    # 6. Exécution en lecture seule ------------------------------------------
    if db is not None:
        rows = execute_readonly(db, sql, max_result_rows, query_timeout_seconds)
        if rows is None:
            issues.append(
                ValidationIssue(
                    rule="execution_echouee",
                    severity="error",
                    message="l'exécution a échoué ou expiré",
                )
            )

    ok = not any(i.severity == "error" for i in issues)
    return ValidationResult(sql=sql, ok=ok, issues=issues)


def explain_cardinality(db: Db, sql: str) -> int | None:
    """`EXPLAIN PLAN` sans exécuter la requête ; retourne la cardinalité estimée
    de la ligne racine du plan, ou `None` si `PLAN_TABLE` est inaccessible.
    """
    statement_id = uuid.uuid4().hex
    db.rows(
        "explain plan",
        f"EXPLAIN PLAN SET STATEMENT_ID = :sid FOR {sql}",
        {"sid": statement_id},
    )
    row = db.rows(
        "cardinalité estimée",
        "SELECT MAX(cardinality) AS c FROM plan_table WHERE statement_id = :sid AND id = 0",
        {"sid": statement_id},
    )
    return row[0]["c"] if row and row[0]["c"] is not None else None


def execute_readonly(db: Db, sql: str, max_rows: int, timeout_seconds: float) -> list[dict] | None:
    """Ajoute une borne `FETCH FIRST` si absente, borne le temps côté serveur.
    Le compte utilisé doit déjà être en lecture seule (contrainte opérationnelle,
    pas appliquée par ce code) — voir le docstring du module.
    """
    bounded = sql if _has_row_limit(sql) else f"{sql.rstrip(';')} FETCH FIRST {max_rows} ROWS ONLY"
    # Label unique : `db.errors` est une map par label, réutilisée sur toute la
    # durée de vie de la connexion (ex. plusieurs tentatives du runtime avec la
    # même session) — un label fixe ferait rater un échec après un premier succès.
    label = f"exécution validée {uuid.uuid4().hex[:8]}"
    rows = db.rows(label, bounded, timeout_ms=int(timeout_seconds * 1000))
    return None if label in db.errors else rows


def _has_row_limit(sql: str) -> bool:
    upper = sql.upper()
    return "FETCH FIRST" in upper or "ROWNUM" in upper
