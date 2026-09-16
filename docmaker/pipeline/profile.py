"""profile : domaines de valeurs, maille, dernier lot — docs/poc-qualite-service.md étape 4.

Trois passes, cadencées différemment selon leur coût (couche-semantique.md §2.3) :

1. **Domaines de valeurs** — `ALL_TAB_COL_STATISTICS` + `ALL_TAB_HISTOGRAMS`
   d'abord (gratuit, déjà calculé par l'optimiseur) ; un scan `GROUP BY` ciblé
   seulement si les statistiques sont absentes ou périmées **et** que la
   colonne appartient à une table du périmètre prioritaire (`build/priority.json`).
2. **Maille** — `COUNT(*) = COUNT(DISTINCT clé)`, uniquement sur le périmètre
   prioritaire (candidats de clé quadratiques en nombre de tables sinon).
3. **Dernier lot** — une colonne de date de chargement (heuristique de nommage
   `DT_…`, cf. dictionnaire d'abréviations) → `MAX`. Coût négligeable (une
   valeur par table), donc appliqué à tout le catalogue, pas seulement au
   périmètre prioritaire.

**Piège du rafraîchissement quotidien** (voir le corps du document) :
« aujourd'hui » n'est presque jamais le dernier jour disponible. Cette étape ne
fait qu'exposer le dernier lot réellement chargé ; c'est au validateur (étape 7)
d'en faire un filtre par défaut plutôt qu'un `SYSDATE` naïf.

Sortie : `build/profile.json`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..config import Settings
from ..models import (
    Catalog,
    CatalogColumn,
    CatalogTable,
    FreshnessResult,
    GrainResult,
    PriorityReport,
    Profile,
    TopValue,
    ValueProfile,
)
from ._oracle import Db, connect, in_clause

log = logging.getLogger(__name__)

_STALE_AFTER = timedelta(days=30)
_DATE_TYPES = ("DATE", "TIMESTAMP")


def run(settings: Settings) -> None:
    build = settings.build_dir
    catalog_path, priority_path = build / "catalog.json", build / "priority.json"
    if not catalog_path.exists():
        raise RuntimeError("build/catalog.json absent — lancer l'étape `catalog` d'abord")
    if not priority_path.exists():
        raise RuntimeError("build/priority.json absent — lancer l'étape `priority` d'abord")
    catalog = Catalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))
    priority = PriorityReport.model_validate_json(priority_path.read_text(encoding="utf-8"))
    priority_scope = set(priority.top(settings.oracle.top_n))

    with connect(settings.oracle, settings.oracle_password) as db:
        values = _value_domains(db, catalog, priority_scope)
        grain = _grain(db, catalog, priority_scope)
        freshness = _freshness(db, catalog)

    profile = Profile(generated_at=datetime.now(), values=values, grain=grain, freshness=freshness)
    path = build / "profile.json"
    path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    log.info(
        "profil : %d domaine(s) de valeurs, %d maille(s) testée(s), %d fraîcheur(s) → %s",
        len(values),
        len(grain),
        len(freshness),
        path,
    )


# --------------------------------------------------------------------------- #
# 1. Domaines de valeurs                                                      #
# --------------------------------------------------------------------------- #
def _value_domains(db: Db, catalog: Catalog, priority_scope: set[str]) -> list[ValueProfile]:
    owners = catalog.owners
    where, binds = in_clause(owners, "o")

    stats = db.rows(
        "statistiques colonnes",
        f"SELECT owner, table_name, column_name, num_distinct, num_nulls, last_analyzed, histogram "
        f"FROM all_tab_col_statistics WHERE owner IN {where}",
        binds,
    )
    stats_by_col = {(r["owner"], r["table_name"], r["column_name"]): r for r in stats}

    histograms = db.rows(
        "histogrammes",
        f"SELECT owner, table_name, column_name, endpoint_number, endpoint_actual_value "
        f"FROM all_tab_histograms WHERE owner IN {where} AND endpoint_actual_value IS NOT NULL "
        f"ORDER BY owner, table_name, column_name, endpoint_number",
        binds,
    )
    top_values_by_col = _top_values_from_histograms(histograms)

    profiles: list[ValueProfile] = []
    for t in catalog.tables:
        for c in t.columns:
            key = (t.owner, t.name, c.name)
            s = stats_by_col.get(key)
            fresh = s and s["last_analyzed"] and datetime.now() - s["last_analyzed"] < _STALE_AFTER
            if s and fresh:
                profiles.append(
                    ValueProfile(
                        column=f"{t.fqn}.{c.name}",
                        source="histogram" if key in top_values_by_col else "stats",
                        n_distinct=s["num_distinct"],
                        n_nulls=s["num_nulls"],
                        top_values=top_values_by_col.get(key, []),
                        last_analyzed=s["last_analyzed"],
                    )
                )
            elif t.fqn in priority_scope:
                scanned = _scan_column(db, t, c)
                if scanned:
                    profiles.append(scanned)
    return profiles


def _top_values_from_histograms(rows: list[dict]) -> dict[tuple[str, str, str], list[TopValue]]:
    """Un histogramme de fréquence donne les valeurs elles-mêmes, avec leur poids
    déductible de l'écart entre `endpoint_number` successifs (nombre de lignes
    représentées par le seau).
    """
    by_col: dict[tuple[str, str, str], list[tuple[int, str]]] = {}
    for r in rows:
        key = (r["owner"], r["table_name"], r["column_name"])
        by_col.setdefault(key, []).append((r["endpoint_number"], str(r["endpoint_actual_value"])))

    result: dict[tuple[str, str, str], list[TopValue]] = {}
    for key, buckets in by_col.items():
        buckets.sort()
        weights = []
        prev = 0
        for num, value in buckets:
            weights.append((value, max(num - prev, 1)))
            prev = num
        total = sum(w for _, w in weights) or 1
        merged: dict[str, int] = {}
        for value, w in weights:
            merged[value] = merged.get(value, 0) + w
        top = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[:10]
        result[key] = [TopValue(value=v, frequency=round(n / total, 4)) for v, n in top]
    return result


def _scan_column(db: Db, table: CatalogTable, column: CatalogColumn) -> ValueProfile | None:
    """Scan `GROUP BY` ciblé — n'est atteint que pour une colonne prioritaire
    dont les statistiques sont absentes ou périmées.
    """
    ident = f'"{table.owner}"."{table.name}"."{column.name}"'
    rows = db.rows(
        f"scan {ident}",
        f'SELECT * FROM (SELECT "{column.name}" AS value, COUNT(*) AS n '
        f'FROM "{table.owner}"."{table.name}" GROUP BY "{column.name}" ORDER BY COUNT(*) DESC) '
        f"WHERE ROWNUM <= 10",
    )
    if not rows:
        return None
    total = sum(r["n"] for r in rows)
    top_values = [TopValue(value=str(r["value"]), frequency=round(r["n"] / total, 4)) for r in rows]
    return ValueProfile(
        column=f"{table.fqn}.{column.name}",
        source="scan",
        n_distinct=None,
        n_nulls=None,
        top_values=top_values,
        last_analyzed=None,
    )


# --------------------------------------------------------------------------- #
# 2. Maille                                                                   #
# --------------------------------------------------------------------------- #
def _grain(db: Db, catalog: Catalog, priority_scope: set[str]) -> list[GrainResult]:
    results = []
    for t in catalog.tables:
        if t.fqn not in priority_scope:
            continue
        key_columns, evidence = _candidate_key(t)
        if not key_columns:
            results.append(
                GrainResult(table=t.fqn, key_columns=[], is_grain=None, evidence="aucune")
            )
            continue
        expr = " || '||' || ".join(f'TO_CHAR("{k}")' for k in key_columns)
        row = db.rows(
            f"maille {t.fqn}",
            f'SELECT COUNT(*) AS n, COUNT(DISTINCT {expr}) AS n_distinct '
            f'FROM "{t.owner}"."{t.name}"',
        )
        is_grain = bool(row) and row[0]["n"] == row[0]["n_distinct"]
        results.append(
            GrainResult(table=t.fqn, key_columns=key_columns, is_grain=is_grain, evidence=evidence)
        )
    return results


def _candidate_key(table: CatalogTable) -> tuple[list[str], str]:
    pk = next((c for c in table.constraints if c.type == "P"), None)
    if pk:
        return pk.columns, "declared_pk"
    unique_idx = next((i for i in table.indexes if i.unique), None)
    if unique_idx:
        return unique_idx.columns, "unique_index"
    return [], "aucune"


# --------------------------------------------------------------------------- #
# 3. Dernier lot                                                              #
# --------------------------------------------------------------------------- #
def _freshness(db: Db, catalog: Catalog) -> list[FreshnessResult]:
    results = []
    for t in catalog.tables:
        col = _load_date_column(t)
        if not col:
            continue
        row = db.rows(
            f"dernier lot {t.fqn}", f'SELECT MAX("{col}") AS m FROM "{t.owner}"."{t.name}"'
        )
        if row:
            results.append(
                FreshnessResult(table=t.fqn, load_date_column=col, max_value=row[0]["m"])
            )
    return results


def _load_date_column(table: CatalogTable) -> str | None:
    """Heuristique de nommage (dictionnaire d'abréviations, couche-semantique.md §2.6) :
    `DT_` = date. Préfère un token évoquant le chargement/traitement (CHG, MAJ, TRT).
    """
    candidates = [
        c for c in table.columns if c.name.upper().startswith("DT_") and _is_date_type(c.data_type)
    ]
    if not candidates:
        return None
    preferred = [c for c in candidates if any(t in c.name.upper() for t in ("CHG", "MAJ", "TRT"))]
    return (preferred or candidates)[0].name


def _is_date_type(data_type: str) -> bool:
    return any(data_type.upper().startswith(t) for t in _DATE_TYPES)
