"""catalog : squelette factuel du datamart — docs/poc-qualite-service.md étape 1.

Lecture seule, comme `recon`. Contrairement à `recon` (qui échantillonne),
`catalog` couvre tout le périmètre : une requête par vue catalogue Oracle, pas
une par table (voir couche-semantique.md §6.1), assemblage en mémoire par FQN
(`OWNER.NAME`, l'identité utilisée par toutes les étapes suivantes).

Sérialisation déterministe : tables/vues/MV triées par FQN, colonnes par
position — `git diff build/catalog.json` devient la veille de dérive du
datamart (colonne ajoutée, type changé, commentaire renseigné, table disparue).

Sortie : `build/catalog.json`. Voir docs/couche-semantique.md §6.1.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from ..config import Settings
from ..models import (
    Catalog,
    CatalogColumn,
    CatalogConstraint,
    CatalogDependency,
    CatalogIndex,
    CatalogMView,
    CatalogProcedure,
    CatalogSynonym,
    CatalogTable,
    CatalogView,
)
from ._oracle import Db, connect, in_clause

log = logging.getLogger(__name__)


def _fqn(owner: str, name: str) -> str:
    return f"{owner}.{name}"


def run(settings: Settings) -> None:
    cfg = settings.oracle
    owners = list(cfg.owners)
    if not owners:
        raise RuntimeError(
            "config.toml: `oracle.owners` doit être figé (lire build/recon.md d'abord)"
        )
    with connect(cfg, settings.oracle_password) as db:
        catalog = _collect(db, owners)

    path = settings.build_dir / "catalog.json"
    path.write_text(catalog.model_dump_json(indent=2), encoding="utf-8")
    log.info(
        "catalogue : %d table(s), %d vue(s), %d vue(s) matérialisée(s) → %s",
        len(catalog.tables),
        len(catalog.views),
        len(catalog.mviews),
        path,
    )


def _collect(db: Db, owners: list[str]) -> Catalog:
    where, binds = in_clause(owners, "o")

    columns_by_holder = _columns(db, where, binds)
    comments = _col_comments(db, where, binds)
    tab_comments = _tab_comments(db, where, binds)
    constraints_by_table = _constraints(db, owners, where, binds)
    indexes_by_table = _indexes(db, where, binds)

    tables = _build_tables(db, where, binds, columns_by_holder, comments, tab_comments)
    for t in tables:
        t.constraints = constraints_by_table.get(t.fqn, [])
        t.indexes = indexes_by_table.get(t.fqn, [])
        _mark_column_keys(t)

    views = _build_views(db, where, binds, columns_by_holder, comments)
    mviews = _build_mviews(db, where, binds, columns_by_holder, comments)
    synonyms = _synonyms(db, owners, where, binds)
    dependencies = _dependencies(db, where, binds)
    procedures = _procedures(db, where, binds)

    return Catalog(
        generated_at=datetime.now(),
        owners=owners,
        tables=sorted(tables, key=lambda t: t.fqn),
        views=sorted(views, key=lambda v: v.fqn),
        mviews=sorted(mviews, key=lambda m: m.fqn),
        synonyms=sorted(synonyms, key=lambda s: s.fqn),
        dependencies=sorted(dependencies, key=lambda d: (d.referenced_fqn, d.dependent_fqn)),
        procedures=sorted(procedures, key=lambda p: p.fqn),
    )


# --------------------------------------------------------------------------- #
# Colonnes — communes tables / vues / MV                                     #
# --------------------------------------------------------------------------- #
def _columns(db: Db, where: str, binds: dict) -> dict[str, list[CatalogColumn]]:
    rows = db.rows(
        "colonnes",
        f"SELECT owner, table_name, column_name, column_id, data_type, data_length, "
        f"data_precision, data_scale, nullable, data_default "
        f"FROM all_tab_columns WHERE owner IN {where} ORDER BY owner, table_name, column_id",
        binds,
    )
    by_holder: dict[str, list[CatalogColumn]] = defaultdict(list)
    for r in rows:
        by_holder[_fqn(r["owner"], r["table_name"])].append(
            CatalogColumn(
                name=r["column_name"],
                position=r["column_id"] or 0,
                data_type=r["data_type"],
                length=r["data_length"],
                precision=r["data_precision"],
                scale=r["data_scale"],
                nullable=r["nullable"] == "Y",
                default=(str(r["data_default"]).strip() if r["data_default"] is not None else None),
            )
        )
    return by_holder


def _col_comments(db: Db, where: str, binds: dict) -> dict[tuple[str, str], str]:
    rows = db.rows(
        "commentaires colonnes",
        f"SELECT owner, table_name, column_name, comments FROM all_col_comments "
        f"WHERE owner IN {where} AND comments IS NOT NULL",
        binds,
    )
    return {  # type: ignore[misc]
        (r["owner"], r["table_name"], r["column_name"]): r["comments"] for r in rows
    }


def _tab_comments(db: Db, where: str, binds: dict) -> dict[str, str]:
    rows = db.rows(
        "commentaires tables",
        f"SELECT owner, table_name, comments FROM all_tab_comments "
        f"WHERE owner IN {where} AND comments IS NOT NULL",
        binds,
    )
    return {_fqn(r["owner"], r["table_name"]): r["comments"] for r in rows}


def _apply_comments(
    fqn: str,
    owner: str,
    name: str,
    columns: list[CatalogColumn],
    col_comments: dict,
    tab_comments: dict[str, str],
) -> str:
    for c in columns:
        key = (owner, name, c.name)
        if key in col_comments:
            c.comment = col_comments[key]
    return tab_comments.get(fqn, "")


# --------------------------------------------------------------------------- #
# Tables                                                                      #
# --------------------------------------------------------------------------- #
def _build_tables(db, where, binds, columns_by_holder, col_comments, tab_comments):
    rows = db.rows(
        "tables",
        f"SELECT owner, table_name, num_rows, partitioned, last_analyzed "
        f"FROM all_tables WHERE owner IN {where}",
        binds,
    )
    tables = []
    for r in rows:
        fqn = _fqn(r["owner"], r["table_name"])
        columns = columns_by_holder.get(fqn, [])
        comment = _apply_comments(
            fqn, r["owner"], r["table_name"], columns, col_comments, tab_comments
        )
        tables.append(
            CatalogTable(
                fqn=fqn,
                owner=r["owner"],
                name=r["table_name"],
                comment=comment,
                partitioned=r["partitioned"] == "YES",
                num_rows=r["num_rows"],
                last_analyzed=r["last_analyzed"],
                columns=columns,
            )
        )
    return tables


# --------------------------------------------------------------------------- #
# Contraintes : PK / FK / U — la cible d'une FK est résolue vers un FQN       #
# --------------------------------------------------------------------------- #
def _constraints(db, owners, where, binds) -> dict[str, list[CatalogConstraint]]:
    cons = db.rows(
        "contraintes",
        f"SELECT owner, constraint_name, constraint_type, table_name, status, validated, "
        f"r_owner, r_constraint_name "
        f"FROM all_constraints WHERE owner IN {where} "
        f"AND constraint_type IN ('P', 'R', 'U')",
        binds,
    )
    cons_cols = db.rows(
        "colonnes de contraintes",
        f"SELECT owner, constraint_name, column_name, position "
        f"FROM all_cons_columns WHERE owner IN {where}",
        binds,
    )
    cols_by_constraint: dict[tuple[str, str], list[str]] = defaultdict(list)
    for c in cons_cols:
        key = (c["owner"], c["constraint_name"])
        cols_by_constraint[key].append((c["position"] or 0, c["column_name"]))
    for key in cols_by_constraint:
        cols_by_constraint[key] = [name for _, name in sorted(cols_by_constraint[key])]

    # Cible des FK : on résout via les PK/U déclarées dans le même périmètre.
    # Une FK pointant hors périmètre (owners) reste non résolue — marquée telle quelle.
    pk_uk_target = {
        (c["owner"], c["constraint_name"]): c["table_name"]
        for c in cons
        if c["constraint_type"] in ("P", "U")
    }

    by_table: dict[str, list[CatalogConstraint]] = defaultdict(list)
    for c in cons:
        fqn = _fqn(c["owner"], c["table_name"])
        r_fqn, r_cols = None, []
        if c["constraint_type"] == "R" and c["r_owner"] and c["r_constraint_name"]:
            target_table = pk_uk_target.get((c["r_owner"], c["r_constraint_name"]))
            if target_table:
                r_fqn = _fqn(c["r_owner"], target_table)
                r_cols = cols_by_constraint.get((c["r_owner"], c["r_constraint_name"]), [])
        by_table[fqn].append(
            CatalogConstraint(
                name=c["constraint_name"],
                type=c["constraint_type"],
                columns=cols_by_constraint.get((c["owner"], c["constraint_name"]), []),
                status=c["status"] or "",
                validated=c["validated"] or "",
                r_constraint_fqn=r_fqn,
                r_columns=r_cols,
            )
        )
    return by_table


def _mark_column_keys(table: CatalogTable) -> None:
    """Reporte `constraint.type` sur `CatalogColumn.key` (PK > FK > U par priorité)."""
    priority = {"P": "PK", "R": "FK", "U": "U"}
    rank = {"PK": 0, "FK": 1, "U": 2, "": 3}
    by_name = {c.name: c for c in table.columns}
    for cons in table.constraints:
        label = priority.get(cons.type)
        if not label:
            continue
        for name in cons.columns:
            col = by_name.get(name)
            if col and rank[label] < rank[col.key or ""]:
                col.key = label


# --------------------------------------------------------------------------- #
# Index                                                                       #
# --------------------------------------------------------------------------- #
def _indexes(db, where, binds) -> dict[str, list[CatalogIndex]]:
    idx = db.rows(
        "index",
        f"SELECT owner, index_name, table_name, uniqueness FROM all_indexes WHERE owner IN {where}",
        binds,
    )
    idx_cols = db.rows(
        "colonnes d'index",
        f"SELECT index_owner AS owner, index_name, column_name, column_position "
        f"FROM all_ind_columns WHERE index_owner IN {where}",
        binds,
    )
    cols_by_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for c in idx_cols:
        key = (c["owner"], c["index_name"])
        cols_by_index[key].append((c["column_position"] or 0, c["column_name"]))
    for key in cols_by_index:
        cols_by_index[key] = [name for _, name in sorted(cols_by_index[key])]

    by_table: dict[str, list[CatalogIndex]] = defaultdict(list)
    for i in idx:
        fqn = _fqn(i["owner"], i["table_name"])
        by_table[fqn].append(
            CatalogIndex(
                name=i["index_name"],
                columns=cols_by_index.get((i["owner"], i["index_name"]), []),
                unique=i["uniqueness"] == "UNIQUE",
            )
        )
    return by_table


# --------------------------------------------------------------------------- #
# Vues / vues matérialisées — texte SQL récupéré en masse (entrée de lineage) #
# --------------------------------------------------------------------------- #
def _build_views(db, where, binds, columns_by_holder, col_comments):
    rows = db.rows(
        "vues", f"SELECT owner, view_name, text FROM all_views WHERE owner IN {where}", binds
    )
    tab_comments = _tab_comments_for_type(db, where, binds, "VIEW")
    views = []
    for r in rows:
        fqn = _fqn(r["owner"], r["view_name"])
        columns = columns_by_holder.get(fqn, [])
        comment = _apply_comments(
            fqn, r["owner"], r["view_name"], columns, col_comments, tab_comments
        )
        views.append(
            CatalogView(
                fqn=fqn,
                owner=r["owner"],
                name=r["view_name"],
                comment=comment,
                columns=columns,
                sql=str(r["text"]) if r["text"] else None,
            )
        )
    return views


def _build_mviews(db, where, binds, columns_by_holder, col_comments):
    rows = db.rows(
        "vues matérialisées",
        f"SELECT owner, mview_name, query FROM all_mviews WHERE owner IN {where}",
        binds,
    )
    tab_comments = _tab_comments_for_type(db, where, binds, "MATERIALIZED VIEW")
    mviews = []
    for r in rows:
        fqn = _fqn(r["owner"], r["mview_name"])
        columns = columns_by_holder.get(fqn, [])
        comment = _apply_comments(
            fqn, r["owner"], r["mview_name"], columns, col_comments, tab_comments
        )
        mviews.append(
            CatalogMView(
                fqn=fqn,
                owner=r["owner"],
                name=r["mview_name"],
                comment=comment,
                columns=columns,
                sql=str(r["query"]) if r["query"] else None,
            )
        )
    return mviews


def _tab_comments_for_type(db, where, binds, table_type: str) -> dict[str, str]:
    rows = db.rows(
        f"commentaires {table_type.lower()}",
        f"SELECT owner, table_name, comments FROM all_tab_comments "
        f"WHERE owner IN {where} AND table_type = :ttype AND comments IS NOT NULL",
        {**binds, "ttype": table_type},
    )
    return {_fqn(r["owner"], r["table_name"]): r["comments"] for r in rows}


# --------------------------------------------------------------------------- #
# Synonymes / dépendances / procédures                                       #
# --------------------------------------------------------------------------- #
def _synonyms(db, owners, where, binds) -> list[CatalogSynonym]:
    rows = db.rows(
        "synonymes",
        f"SELECT owner, synonym_name, table_owner, table_name FROM all_synonyms "
        f"WHERE table_owner IN {where}",
        binds,
    )
    return [
        CatalogSynonym(
            fqn=_fqn(r["owner"], r["synonym_name"]),
            target_fqn=_fqn(r["table_owner"], r["table_name"]),
        )
        for r in rows
        if r["owner"] in owners or r["owner"] == "PUBLIC"
    ]


def _dependencies(db, where, binds) -> list[CatalogDependency]:
    rows = db.rows(
        "dépendances",
        f"SELECT referenced_owner, referenced_name, owner, name, type "
        f"FROM all_dependencies WHERE referenced_owner IN {where} "
        f"AND referenced_type IN ('TABLE', 'VIEW')",
        binds,
    )
    return [
        CatalogDependency(
            referenced_fqn=_fqn(r["referenced_owner"], r["referenced_name"]),
            dependent_fqn=_fqn(r["owner"], r["name"]),
            dependent_type=r["type"],
        )
        for r in rows
    ]


def _procedures(db, where, binds) -> list[CatalogProcedure]:
    rows = db.rows(
        "sources PL/SQL",
        f"SELECT owner, name, type, COUNT(*) AS n_lines FROM all_source "
        f"WHERE owner IN {where} GROUP BY owner, name, type",
        binds,
    )
    return [
        CatalogProcedure(fqn=_fqn(r["owner"], r["name"]), type=r["type"], n_lines=r["n_lines"])
        for r in rows
    ]
