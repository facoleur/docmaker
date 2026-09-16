"""priority : classement de centralité — docs/poc-qualite-service.md étape 2.

Sans logs de requêtes ni questions métier connues, l'annotation LLM partirait
sur des centaines de tables indifféremment. Cinq signaux, **jamais fondus en un
score opaque** (couche-semantique.md §2.5) : chaque signal reste lisible à côté
du score composite, pour rester défendable devant un DBA.

Le score composite est la moyenne des rangs percentiles des signaux
*disponibles* pour la table (un signal absent n'est jamais mis à zéro — ça
pénaliserait à tort une table simplement non instrumentée).

Entrée : `build/catalog.json` (volume, fraîcheur) + Oracle (exposition,
activité, fan-out). Sortie : `build/priority.json`.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..config import Settings
from ..models import Catalog, PriorityEntry, PriorityReport, PrioritySignals
from ._oracle import Db, connect, in_clause

log = logging.getLogger(__name__)


def run(settings: Settings) -> None:
    catalog_path = settings.build_dir / "catalog.json"
    if not catalog_path.exists():
        raise RuntimeError("build/catalog.json absent — lancer l'étape `catalog` d'abord")
    catalog = Catalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))

    cfg = settings.oracle
    with connect(cfg, settings.oracle_password) as db:
        exposure, activity, fanout = _live_signals(db, cfg.owners)

    report = build_report(catalog, exposure, activity, fanout)

    path = settings.build_dir / "priority.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    log.info(
        "priorisation : %d table(s) classées, top %d écrit → %s",
        len(report.entries),
        cfg.top_n,
        path,
    )
    log.info("top %d : %s", cfg.top_n, ", ".join(report.top(cfg.top_n)[:10]))


def _live_signals(
    db: Db, owners: list[str]
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    where, binds = in_clause(owners, "o")

    exposure_rows = db.rows(
        "exposition",
        f"SELECT table_schema, table_name, COUNT(DISTINCT grantee) AS n "
        f"FROM all_tab_privs WHERE privilege = 'SELECT' AND table_schema IN {where} "
        f"GROUP BY table_schema, table_name",
        binds,
    )
    exposure = {f"{r['table_schema']}.{r['table_name']}": r["n"] for r in exposure_rows}

    activity_rows = db.rows(
        "activité de chargement",
        f"SELECT table_owner, table_name, "
        f"NVL(inserts, 0) + NVL(updates, 0) + NVL(deletes, 0) AS n "
        f"FROM all_tab_modifications WHERE table_owner IN {where}",
        binds,
    )
    activity = {f"{r['table_owner']}.{r['table_name']}": r["n"] for r in activity_rows}

    fanout_rows = db.rows(
        "fan-out",
        f"SELECT referenced_owner, referenced_name, COUNT(*) AS n FROM all_dependencies "
        f"WHERE referenced_owner IN {where} AND referenced_type = 'TABLE' "
        f"GROUP BY referenced_owner, referenced_name",
        binds,
    )
    fanout = {f"{r['referenced_owner']}.{r['referenced_name']}": r["n"] for r in fanout_rows}

    return exposure, activity, fanout


def build_report(
    catalog: Catalog,
    exposure: dict[str, int],
    activity: dict[str, int],
    fanout: dict[str, int],
) -> PriorityReport:
    """Pure (hors accès I/O) : testable sans base Oracle."""
    now = datetime.now()
    signals_by_fqn: dict[str, PrioritySignals] = {}
    for t in catalog.tables:
        freshness_days = (now - t.last_analyzed).days if t.last_analyzed else None
        signals_by_fqn[t.fqn] = PrioritySignals(
            exposure=exposure.get(t.fqn),
            activity=activity.get(t.fqn),
            fanout=fanout.get(t.fqn),
            volume=t.num_rows,
            freshness_days=freshness_days,
        )

    ranks = {
        "exposure": _percentile_ranks({f: s.exposure for f, s in signals_by_fqn.items()}),
        "activity": _percentile_ranks({f: s.activity for f, s in signals_by_fqn.items()}),
        "fanout": _percentile_ranks({f: s.fanout for f, s in signals_by_fqn.items()}),
        "volume": _percentile_ranks({f: s.volume for f, s in signals_by_fqn.items()}),
        "freshness_days": _percentile_ranks(
            {f: s.freshness_days for f, s in signals_by_fqn.items()}, higher_is_better=False
        ),
    }

    entries = []
    for fqn, signals in signals_by_fqn.items():
        available = [ranks[name][fqn] for name in ranks if fqn in ranks[name]]
        score = sum(available) / len(available) if available else 0.0
        entries.append(PriorityEntry(fqn=fqn, signals=signals, score=round(score, 4)))

    entries.sort(key=lambda e: e.score, reverse=True)
    return PriorityReport(generated_at=now, entries=entries)


def _percentile_ranks(
    values: dict[str, float | None], *, higher_is_better: bool = True
) -> dict[str, float]:
    """Rang percentile dans [0, 1] pour chaque clé dont la valeur n'est pas `None`.

    Une clé absente du résultat = signal indisponible pour cette table.
    """
    present = {k: v for k, v in values.items() if v is not None}
    if len(present) <= 1:
        return dict.fromkeys(present, 1.0)
    ordered = sorted(present, key=lambda k: present[k])
    n = len(ordered) - 1
    ranks = {k: i / n for i, k in enumerate(ordered)}
    if not higher_is_better:
        ranks = {k: 1 - r for k, r in ranks.items()}
    return ranks
