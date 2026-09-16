"""joins : retrouver les FK non déclarées — docs/poc-qualite-service.md étape 4.

Sur un datamart, les FK sont rarement déclarées (chargement ELT, contraintes
désactivées pour la performance — couche-semantique.md §2.4). Cette étape
combine trois sources de candidats, puis les **vérifie** par une requête :

- `declared_fk` — les contraintes `R` du catalogue, même désactivées/non
  validées : ce sont les candidats les plus fiables ;
- `observed_lineage` — les arêtes du graphe de jointures de `lineage.json`
  (ce que le SQL fait réellement) ;
- `name_match` — deux colonnes de même nom dans deux tables du périmètre
  prioritaire (repli le plus faible, borné au périmètre pour rester tractable
  — le test d'inclusion est quadratique en nombre de couples).

Vérification, pour chaque candidat : test d'inclusion `A.X ⊆ B.X` (en ignorant
les `NULL`, qui ne sont jamais des clés de jointure valides) + unicité de
chaque côté → la cardinalité (`1:1`, `1:N`, `N:1`, `N:N`).

Sortie : `build/joins.json`.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..config import Settings
from ..models import Catalog, CatalogTable, JoinCandidate, Joins, Lineage, PriorityReport
from ._oracle import Db, connect

log = logging.getLogger(__name__)


def run(settings: Settings) -> None:
    build = settings.build_dir
    paths = {name: build / f"{name}.json" for name in ("catalog", "priority", "lineage")}
    missing = [n for n, p in paths.items() if not p.exists()]
    if missing:
        raise RuntimeError(
            f"artefact(s) manquant(s) : {missing} — lancer les étapes correspondantes"
        )

    catalog = Catalog.model_validate_json(paths["catalog"].read_text(encoding="utf-8"))
    priority = PriorityReport.model_validate_json(paths["priority"].read_text(encoding="utf-8"))
    lineage = Lineage.model_validate_json(paths["lineage"].read_text(encoding="utf-8"))
    priority_scope = set(priority.top(settings.oracle.top_n))

    candidates = build_candidates(catalog, lineage, priority_scope)
    with connect(settings.oracle, settings.oracle_password) as db:
        verified = [_verify(db, c) for c in candidates]

    joins = Joins(generated_at=datetime.now(), candidates=verified)
    path = build / "joins.json"
    path.write_text(joins.model_dump_json(indent=2), encoding="utf-8")
    n_included = sum(1 for c in verified if c.included)
    log.info(
        "jointures : %d candidat(e)s testé(e)s, %d confirmé(e)s → %s",
        len(verified),
        n_included,
        path,
    )


def declared_fk_pairs(catalog: Catalog) -> list[tuple[str, str]]:
    pairs = []
    for t in catalog.tables:
        for cons in t.constraints:
            if cons.type != "R" or not cons.r_constraint_fqn or not cons.r_columns:
                continue
            for left_col, right_col in zip(cons.columns, cons.r_columns, strict=False):
                pairs.append((f"{t.fqn}.{left_col}", f"{cons.r_constraint_fqn}.{right_col}"))
    return pairs


def lineage_pairs(lineage: Lineage) -> list[tuple[str, str]]:
    return [(edge.left, edge.right) for edge in lineage.joins]


def name_match_pairs(catalog: Catalog, priority_scope: set[str]) -> list[tuple[str, str]]:
    by_column_name: dict[str, list[CatalogTable]] = {}
    for t in catalog.tables:
        if t.fqn not in priority_scope:
            continue
        for c in t.columns:
            by_column_name.setdefault(c.name.upper(), []).append(t)
    pairs = []
    for name, tables in by_column_name.items():
        for i, t1 in enumerate(tables):
            for t2 in tables[i + 1 :]:
                pairs.append((f"{t1.fqn}.{name}", f"{t2.fqn}.{name}"))
    return pairs


def build_candidates(
    catalog: Catalog, lineage: Lineage, priority_scope: set[str]
) -> list[JoinCandidate]:
    """Pure (hors I/O) : dédoublonne les trois sources sur la paire de colonnes.

    Priorité d'évidence en cas de doublon : `declared_fk` > `observed_lineage`
    > `name_match` — la plus fiable l'emporte dans l'étiquette, mais ça ne veut
    pas dire que les autres sources ne l'auraient pas trouvée aussi (c'est
    exactement ce que mesure `docmaker/eval/masking.py`, mesure 2).
    """
    seen: dict[frozenset[str], JoinCandidate] = {}

    def add(left: str, right: str, evidence: str) -> None:
        key = frozenset((left, right))
        if key not in seen:
            seen[key] = JoinCandidate(left=left, right=right, evidence=evidence)

    for left, right in declared_fk_pairs(catalog):
        add(left, right, "declared_fk")
    for left, right in lineage_pairs(lineage):
        add(left, right, "observed_lineage")
    for left, right in name_match_pairs(catalog, priority_scope):
        add(left, right, "name_match")

    return sorted(seen.values(), key=lambda c: (c.left, c.right))


def _split(fqn_column: str) -> tuple[str, str]:
    owner, table, column = fqn_column.split(".")
    return f"{owner}.{table}", column


def _verify(db: Db, candidate: JoinCandidate) -> JoinCandidate:
    left_table, left_col = _split(candidate.left)
    right_table, right_col = _split(candidate.right)
    left_owner, left_name = left_table.split(".")
    right_owner, right_name = right_table.split(".")

    included = db.scalar(
        f"inclusion {candidate.left} ⊆ {candidate.right}",
        f'SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END FROM ('
        f'  SELECT "{left_col}" FROM "{left_owner}"."{left_name}" '
        f'  WHERE "{left_col}" IS NOT NULL'
        f"  MINUS"
        f'  SELECT "{right_col}" FROM "{right_owner}"."{right_name}" '
        f'  WHERE "{right_col}" IS NOT NULL'
        f")",
    )
    left_unique = _is_unique(db, left_owner, left_name, left_col)
    right_unique = _is_unique(db, right_owner, right_name, right_col)

    return candidate.model_copy(
        update={
            "included": bool(included) if included is not None else None,
            "left_unique": left_unique,
            "right_unique": right_unique,
        }
    )


def _is_unique(db: Db, owner: str, table: str, column: str) -> bool | None:
    row = db.rows(
        f"unicité {owner}.{table}.{column}",
        f'SELECT COUNT(*) AS n, COUNT(DISTINCT "{column}") AS n_distinct '
        f'FROM "{owner}"."{table}" WHERE "{column}" IS NOT NULL',
    )
    if not row:
        return None
    return row[0]["n"] == row[0]["n_distinct"]
