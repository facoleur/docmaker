"""lineage : le gisement de sens — docs/poc-qualite-service.md étape 3.

À cette échelle, la source porte l'essentiel du sens (`DIM_X.FLG_Y` ne veut
rien dire ; l'expression qui l'alimente le dit — couche-semantique.md §2.2).
`sqlglot` (dialecte `oracle`) parse le texte des vues et vues matérialisées du
catalogue pour en tirer, par sous-produit du même arbre :

- l'expression de calcul de chaque colonne dérivée, remontée à ses colonnes
  amont (lineage colonne à colonne) ;
- le graphe de jointures réel, pondéré par fréquence d'apparition ;
- les colonnes candidates « mortes ».

**Portée assumée et explicite** (à ne jamais présenter comme une couverture
complète — voir `couche-semantique.md` §5.3) :

- seuls les *vues* et *vues matérialisées* du catalogue sont parsées ici ;
  le SQL vivant dans les procédures/packages (`ALL_SOURCE`) ne l'est pas —
  il est souvent dynamique (`EXECUTE IMMEDIATE`), donc peu parsable, et c'est
  un chantier séparé. `ALL_DEPENDENCIES` (dans `catalog.json`) donne déjà le
  lineage table à table pour ces objets, sans parseur.
- Les CTE et sous-requêtes imbriquées sont vues par le parseur mais leurs
  alias ne sont pas résolus vers de vraies tables : leurs colonnes apparaissent
  dans le lineage avec un FQN qui ne matchera pas le catalogue. Sans
  conséquence sur le taux de parsing (mesuré), seulement sur la profondeur de
  résolution.
- Une colonne « morte » ici signifie seulement : jamais lue par le SQL parsé
  (vues/MV). Elle peut être lue ailleurs (application, export). C'est un
  candidat à vérifier, pas un verdict.

`parse_stats.unparsed_ratio` doit toujours accompagner tout usage du lineage
en aval — jamais de couverture implicitement complète (étape 1, critère).

Sortie : `build/lineage.json`.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from ..models import Catalog, ColumnLineage, DeadColumn, JoinEdge, Lineage, ParseStats

log = logging.getLogger(__name__)


def run(settings) -> None:
    catalog_path = settings.build_dir / "catalog.json"
    if not catalog_path.exists():
        raise RuntimeError("build/catalog.json absent — lancer l'étape `catalog` d'abord")
    catalog = Catalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))

    lineage = build_lineage(catalog)

    path = settings.build_dir / "lineage.json"
    path.write_text(lineage.model_dump_json(indent=2), encoding="utf-8")
    log.info(
        "lineage : %d expression(s) colonne, %d arête(s) de jointure, "
        "%d colonne(s) morte(s) candidate(s), taux non parsé %.0f%% → %s",
        len(lineage.columns),
        len(lineage.joins),
        len(lineage.dead_columns),
        lineage.parse_stats.unparsed_ratio * 100,
        path,
    )


def build_lineage(catalog: Catalog) -> Lineage:
    """Pure (hors I/O) : testable en construisant un `Catalog` en mémoire."""
    import sqlglot
    from sqlglot import exp

    known_fqns = catalog.all_table_fqns()
    join_freq: dict[tuple[str, str], int] = defaultdict(int)
    join_samples: dict[tuple[str, str], list[str]] = defaultdict(list)
    columns: list[ColumnLineage] = []
    referenced: set[str] = set()
    failures: list[str] = []
    n_objects = 0

    objects = [(v.fqn, v.owner, v.sql) for v in catalog.views if v.sql] + [
        (m.fqn, m.owner, m.sql) for m in catalog.mviews if m.sql
    ]

    for object_fqn, owner, sql in objects:
        n_objects += 1
        try:
            tree = sqlglot.parse_one(sql, dialect="oracle")
        except Exception as exc:  # noqa: BLE001 — un échec de parsing est une mesure
            log.debug("échec de parsing sur %s : %s", object_fqn, exc)
            failures.append(object_fqn)
            continue

        alias_map = _alias_map(tree, exp, catalog, known_fqns, owner)

        for join in tree.find_all(exp.Join):
            for eq in join.find_all(exp.EQ):
                left, right = _resolve_column(eq.left, alias_map, exp), _resolve_column(
                    eq.right, alias_map, exp
                )
                if left and right and left != right:
                    key = tuple(sorted((left, right)))
                    join_freq[key] += 1
                    if object_fqn not in join_samples[key]:
                        join_samples[key].append(object_fqn)
                    referenced.update(key)

        for selected in _select_expressions(tree, exp):
            target_name = selected.alias_or_name
            if not target_name:
                continue
            sources = sorted(
                {
                    r
                    for c in selected.find_all(exp.Column)
                    if (r := _resolve_column(c, alias_map, exp))
                }
            )
            referenced.update(sources)
            columns.append(
                ColumnLineage(
                    target=f"{object_fqn}.{target_name.upper()}",
                    expression=selected.sql(dialect="oracle"),
                    sources=sources,
                    object_fqn=object_fqn,
                )
            )

    dead = sorted(
        (
            DeadColumn(fqn=f"{t.fqn}.{c.name}")
            for t in catalog.tables
            for c in t.columns
            if f"{t.fqn}.{c.name}" not in referenced
        ),
        key=lambda d: d.fqn,
    )

    joins = sorted(
        (
            JoinEdge(left=k[0], right=k[1], frequency=n, sample_objects=join_samples[k][:5])
            for k, n in join_freq.items()
        ),
        key=lambda j: (-j.frequency, j.left, j.right),
    )

    return Lineage(
        generated_at=datetime.now(),
        columns=sorted(columns, key=lambda c: c.target),
        joins=joins,
        dead_columns=dead,
        parse_stats=ParseStats(
            n_objects=n_objects,
            n_parsed=n_objects - len(failures),
            n_failed=len(failures),
            failures=sorted(failures),
        ),
    )


def _alias_map(
    tree, exp, catalog: Catalog, known_fqns: set[str], default_owner: str
) -> dict[str, str]:
    """alias-ou-nom (minuscule) -> FQN résolu, pour toutes les tables du FROM/JOIN."""
    mapping: dict[str, str] = {}
    for t in tree.find_all(exp.Table):
        key = (t.alias or t.name).lower()
        mapping[key] = _resolve_table_fqn(t, catalog, known_fqns, default_owner)
    return mapping


def _resolve_table_fqn(
    table_exp, catalog: Catalog, known_fqns: set[str], default_owner: str
) -> str:
    name = table_exp.name.upper()
    owner = (table_exp.db or default_owner).upper()
    fqn = f"{owner}.{name}"
    if fqn in known_fqns:
        return fqn
    for syn in catalog.synonyms:
        syn_owner, _, syn_name = syn.fqn.partition(".")
        if syn_name == name and (not table_exp.db or syn_owner == owner):
            return syn.target_fqn
    return fqn  # meilleure estimation ; peut ne pas exister (CTE, table hors périmètre)


def _resolve_column(node, alias_map: dict[str, str], exp) -> str | None:
    if not isinstance(node, exp.Column):
        return None
    table_key = (node.table or "").lower()
    if table_key and table_key in alias_map:
        return f"{alias_map[table_key]}.{node.name.upper()}"
    if not table_key and len(alias_map) == 1:
        return f"{next(iter(alias_map.values()))}.{node.name.upper()}"
    return None  # référence ambiguë (plusieurs tables, pas de qualificateur) — non résolue


def _select_expressions(tree, exp):
    """Les expressions de la clause SELECT la plus externe (ignore les sous-requêtes)."""
    select = tree if isinstance(tree, exp.Select) else next(iter(tree.find_all(exp.Select)), None)
    return select.expressions if select else []
