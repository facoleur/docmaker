"""recon : passe de reconnaissance en lecture seule sur le datamart Oracle.

Ne produit AUCUNE documentation. Elle mesure le terrain pour que les étapes
suivantes soient dimensionnées sur des chiffres et non sur des hypothèses :
périmètre, volume, taux de commentaires natifs, fraîcheur des statistiques,
part de la transformation qui vit dans la base, et surtout le taux de parsing
`sqlglot` sur des vues réelles — le test qui valide ou invalide le lineage
colonne à colonne.

Garanties, pour qu'elle soit acceptable sur un environnement de production :
aucun appel LLM, aucune écriture en base, aucune lecture de donnée métier —
uniquement des métadonnées (vues `ALL_*`).

Sorties : `build/recon.json` (brut, rejouable) et `build/recon.md` (rapport).

Voir docs/couche-semantique.md §5 et §7.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from ..config import Settings

log = logging.getLogger(__name__)

# Schémas maintenus par Oracle, écartés quand `owners` n'est pas fixé en config.
# Filet de sécurité : on préfère `all_users.oracle_maintained` quand il existe.
_SYSTEM_SCHEMAS = {
    "SYS",
    "SYSTEM",
    "SYSBACKUP",
    "SYSDG",
    "SYSKM",
    "SYSRAC",
    "SYS$UMF",
    "XDB",
    "CTXSYS",
    "MDSYS",
    "OLAPSYS",
    "ORDSYS",
    "ORDDATA",
    "ORDPLUGINS",
    "OUTLN",
    "WMSYS",
    "DBSNMP",
    "APPQOSSYS",
    "AUDSYS",
    "DVSYS",
    "DVF",
    "GSMADMIN_INTERNAL",
    "GSMCATUSER",
    "GSMUSER",
    "LBACSYS",
    "OJVMSYS",
    "DBSFWUSER",
    "REMOTE_SCHEDULER_AGENT",
    "ANONYMOUS",
    "PUBLIC",
    "EXFSYS",
    "ORACLE_OCM",
    "MGMT_VIEW",
    "SI_INFORMTN_SCHEMA",
    "FLOWS_FILES",
    "SPATIAL_CSW_ADMIN_USR",
    "SPATIAL_WFS_ADMIN_USR",
    "DIP",
    "TSMSYS",
}
_SYSTEM_PREFIXES = ("APEX_", "FLOWS_", "OWBSYS")

# Types de dépendance qui écrivent dans une table (par opposition à la lire).
_WRITER_TYPES = {"PROCEDURE", "FUNCTION", "PACKAGE", "PACKAGE BODY", "TRIGGER", "JOB"}


# --------------------------------------------------------------------------- #
# Accès base                                                                  #
# --------------------------------------------------------------------------- #
class _Db:
    """Connexion Oracle en lecture seule, tolérante aux privilèges manquants.

    Une vue `ALL_*` inaccessible n'interrompt pas la passe : elle est consignée
    comme un manque dans le rapport — c'est en soi un résultat.
    """

    def __init__(self, conn) -> None:
        self._conn = conn
        self.errors: dict[str, str] = {}

    def rows(self, label: str, sql: str, binds: dict | None = None) -> list[dict[str, Any]]:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, binds or {})
                cols = [d[0].lower() for d in cur.description]
                return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001 — on veut consigner, pas planter
            msg = str(exc).splitlines()[0][:200]
            log.warning("requête %s indisponible : %s", label, msg)
            self.errors[label] = msg
            return []

    def scalar(self, label: str, sql: str, binds: dict | None = None):
        rows = self.rows(label, sql, binds)
        return next(iter(rows[0].values())) if rows else None


def _in_clause(values: list[str], prefix: str) -> tuple[str, dict[str, str]]:
    """Construit `(:p0, :p1, …)` et les binds associés (Oracle refuse une liste)."""
    names = [f"{prefix}{i}" for i in range(len(values))]
    return "(" + ", ".join(f":{n}" for n in names) + ")", dict(zip(names, values, strict=True))


# --------------------------------------------------------------------------- #
# Étape                                                                       #
# --------------------------------------------------------------------------- #
def run(settings: Settings) -> None:
    try:
        import oracledb
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("dépendance manquante : `poetry add oracledb`") from exc

    cfg = settings.oracle
    if not settings.oracle_password:
        raise RuntimeError("ORACLE_PASSWORD absent de l'environnement (ou du .env)")

    log.info("connexion à %s (mode thin) …", cfg.dsn)
    conn = oracledb.connect(user=cfg.user, password=settings.oracle_password, dsn=cfg.dsn)
    try:
        report = _collect(_Db(conn), cfg)
    finally:
        conn.close()

    (settings.build_dir / "recon.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    md = _render(report, cfg)
    (settings.build_dir / "recon.md").write_text(md, encoding="utf-8")
    log.info("rapport : %s", settings.build_dir / "recon.md")


def _collect(db: _Db, cfg) -> dict[str, Any]:
    rep: dict[str, Any] = {"generated_at": datetime.now().isoformat(timespec="seconds")}
    rep["db_version"] = db.scalar(
        "version",
        "SELECT version FROM product_component_version WHERE product LIKE 'Oracle%'",
    )

    # 1. Périmètre et volume ------------------------------------------------
    inventory = db.rows(
        "inventaire",
        "SELECT owner, object_type, COUNT(*) AS n FROM all_objects "
        "GROUP BY owner, object_type ORDER BY owner, object_type",
    )
    rep["inventory"] = inventory
    owners = list(cfg.owners) or _discover_owners(db, inventory)
    rep["owners"] = owners
    rep["owners_auto"] = not cfg.owners
    if not owners:
        log.error("aucun schéma applicatif détecté — renseigner `owners` dans config.toml")
        rep["errors"] = db.errors
        return rep
    log.info("périmètre : %s", ", ".join(owners))
    where, binds = _in_clause(owners, "o")

    rep["columns"] = db.rows(
        "colonnes",
        f"SELECT owner, COUNT(DISTINCT table_name) AS n_objects, COUNT(*) AS n_columns "
        f"FROM all_tab_columns WHERE owner IN {where} GROUP BY owner ORDER BY owner",
        binds,
    )
    rep["tables"] = db.rows(
        "tables",
        f"SELECT owner, COUNT(*) AS n_tables, "
        f"SUM(CASE WHEN partitioned = 'YES' THEN 1 ELSE 0 END) AS n_partitioned, "
        f"SUM(num_rows) AS n_rows_est "
        f"FROM all_tables WHERE owner IN {where} GROUP BY owner ORDER BY owner",
        binds,
    )

    # 2. Commentaires natifs — l'enjeu majeur du §5.1 ------------------------
    rep["col_comments"] = db.rows(
        "commentaires colonnes",
        f"SELECT c.owner, COUNT(*) AS n_columns, COUNT(cc.comments) AS n_commented "
        f"FROM all_tab_columns c "
        f"LEFT JOIN all_col_comments cc ON cc.owner = c.owner "
        f"  AND cc.table_name = c.table_name AND cc.column_name = c.column_name "
        f"WHERE c.owner IN {where} GROUP BY c.owner ORDER BY c.owner",
        binds,
    )
    rep["tab_comments"] = db.rows(
        "commentaires tables",
        f"SELECT owner, COUNT(*) AS n_objects, COUNT(comments) AS n_commented "
        f"FROM all_tab_comments WHERE owner IN {where} GROUP BY owner ORDER BY owner",
        binds,
    )

    # 3. Contraintes : combien de FK réelles ? -------------------------------
    rep["constraints"] = db.rows(
        "contraintes",
        f"SELECT owner, constraint_type, status, validated, COUNT(*) AS n "
        f"FROM all_constraints WHERE owner IN {where} "
        f"GROUP BY owner, constraint_type, status, validated ORDER BY owner, constraint_type",
        binds,
    )

    # 4. Statistiques : le profiling gratuit est-il possible ? ---------------
    rep["stats"] = db.rows(
        "statistiques",
        f"SELECT owner, COUNT(*) AS n_columns, COUNT(last_analyzed) AS n_analyzed, "
        f"MIN(last_analyzed) AS oldest, MAX(last_analyzed) AS newest, "
        f"SUM(CASE WHEN histogram IS NOT NULL AND histogram <> 'NONE' THEN 1 ELSE 0 END) "
        f"  AS n_histograms "
        f"FROM all_tab_col_statistics WHERE owner IN {where} GROUP BY owner ORDER BY owner",
        binds,
    )

    # 5. Dépendances : lineage table à table, gratuit ------------------------
    deps = db.rows(
        "dépendances",
        f"SELECT referenced_owner, referenced_name, type AS dependent_type, COUNT(*) AS n "
        f"FROM all_dependencies WHERE referenced_owner IN {where} AND referenced_type = 'TABLE' "
        f"GROUP BY referenced_owner, referenced_name, type",
        binds,
    )
    rep["top_referenced"] = db.rows(
        "centralité",
        f"SELECT * FROM (SELECT referenced_owner, referenced_name, COUNT(*) AS n_dependents "
        f"FROM all_dependencies WHERE referenced_owner IN {where} AND referenced_type = 'TABLE' "
        f"GROUP BY referenced_owner, referenced_name ORDER BY COUNT(*) DESC) "
        f"WHERE ROWNUM <= :top",
        {**binds, "top": cfg.top_n},
    )
    all_tables = db.rows(
        "liste tables",
        f"SELECT owner, table_name FROM all_tables WHERE owner IN {where}",
        binds,
    )
    rep["blind_spot"] = _blind_spot(all_tables, deps)

    # 6. Exposition : ce qui est donné en lecture aux consommateurs ----------
    rep["grants"] = db.rows(
        "privilèges",
        f"SELECT * FROM (SELECT grantee, COUNT(*) AS n_objects FROM all_tab_privs "
        f"WHERE privilege = 'SELECT' AND table_schema IN {where} "
        f"GROUP BY grantee ORDER BY COUNT(*) DESC) WHERE ROWNUM <= :top",
        {**binds, "top": cfg.top_n},
    )
    rep["synonyms"] = db.scalar(
        "synonymes",
        f"SELECT COUNT(*) FROM all_synonyms WHERE table_owner IN {where}",
        binds,
    )

    # 7. Le test décisif : sqlglot parse-t-il ce SQL Oracle ? ----------------
    rep["parse"] = _parse_test(db, owners, cfg.sample_views)

    rep["errors"] = db.errors
    return rep


def _discover_owners(db: _Db, inventory: list[dict]) -> list[str]:
    """Schémas applicatifs : `oracle_maintained` si disponible, sinon liste noire."""
    rows = db.rows(
        "schémas applicatifs",
        "SELECT username FROM all_users WHERE oracle_maintained = 'N' ORDER BY username",
    )
    if rows:
        declared = {r["username"] for r in rows}
    else:  # 11g, ou colonne inaccessible
        declared = None
    seen = {r["owner"] for r in inventory}
    return sorted(
        o
        for o in seen
        if (o in declared if declared is not None else True)
        and o not in _SYSTEM_SCHEMAS
        and not o.startswith(_SYSTEM_PREFIXES)
    )


def _blind_spot(all_tables: list[dict], deps: list[dict]) -> dict[str, int]:
    """Part de la transformation invisible depuis la base (docs §5.3).

    Une table qu'aucun objet de la base ne référence n'est ni alimentée ni lue
    en SQL : elle est chargée de l'extérieur. Une table lue par des vues mais
    référencée par aucun objet PL/SQL est probablement alimentée hors base.
    """
    by_table: dict[tuple[str, str], set[str]] = {}
    for d in deps:
        by_table.setdefault((d["referenced_owner"], d["referenced_name"]), set()).add(
            d["dependent_type"]
        )
    orphan = read_only = written = 0
    for t in all_tables:
        types = by_table.get((t["owner"], t["table_name"]))
        if not types:
            orphan += 1
        elif types & _WRITER_TYPES:
            written += 1
        else:
            read_only += 1
    return {
        "n_tables": len(all_tables),
        "orphan": orphan,
        "read_only": read_only,
        "written_in_db": written,
    }


def _parse_test(db: _Db, owners: list[str], sample: int) -> dict[str, Any]:
    """Tire un échantillon de vues réparti par taille et le passe à sqlglot."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("dépendance manquante : `poetry add sqlglot`") from exc

    where, binds = _in_clause(owners, "o")
    catalog = db.rows(
        "vues",
        f"SELECT owner, view_name, text_length FROM all_views WHERE owner IN {where} "
        f"ORDER BY text_length",
        binds,
    )
    mviews = db.scalar(
        "vues matérialisées",
        f"SELECT COUNT(*) FROM all_mviews WHERE owner IN {where}",
        binds,
    )
    if not catalog:
        return {"n_views": 0, "n_mviews": mviews, "tested": 0, "ok": 0, "failures": []}

    # Échantillon réparti sur toute la plage de tailles : une vue de 30 lignes
    # et une vue de 800 lignes ne posent pas les mêmes problèmes au parseur.
    step = max(1, len(catalog) // sample)
    picked = catalog[::step][:sample]

    ok, failures, tables_found = 0, [], 0
    for v in picked:
        sql = db.scalar(
            "texte de vue",
            "SELECT text FROM all_views WHERE owner = :ow AND view_name = :vn",
            {"ow": v["owner"], "vn": v["view_name"]},
        )
        if not sql:
            failures.append({**_ident(v), "error": "texte illisible (LONG)"})
            continue
        try:
            tree = sqlglot.parse_one(str(sql), dialect="oracle")
            tables_found += len({t.sql() for t in tree.find_all(exp.Table)})
            ok += 1
        except Exception as exc:  # noqa: BLE001 — un échec de parsing est une mesure
            failures.append({**_ident(v), "error": str(exc).splitlines()[0][:180]})

    return {
        "n_views": len(catalog),
        "n_mviews": mviews,
        "tested": len(picked),
        "ok": ok,
        "tables_extracted": tables_found,
        "failures": failures,
    }


def _ident(v: dict) -> dict[str, Any]:
    return {"view": f"{v['owner']}.{v['view_name']}", "text_length": v["text_length"]}


# --------------------------------------------------------------------------- #
# Rapport                                                                     #
# --------------------------------------------------------------------------- #
def _pct(num: int | None, den: int | None) -> str:
    if not den or num is None:
        return "—"
    return f"{100 * num / den:.0f} %"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_aucune donnée_\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join("—" if c is None else str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def _render(r: dict[str, Any], cfg) -> str:
    p: list[str] = []
    a = p.append
    a(f"# Reconnaissance du datamart — {r['generated_at']}\n")
    a(
        f"Oracle `{r.get('db_version') or '?'}` · périmètre "
        f"{'découvert automatiquement' if r.get('owners_auto') else 'fixé en configuration'} : "
        f"**{', '.join(r.get('owners') or ['—'])}**\n"
    )
    a(
        "> Rapport de mesure, pas de documentation. Chaque section indique ce que le\n"
        "> chiffre décide pour la suite. Voir `docs/couche-semantique.md`.\n"
    )

    if not r.get("owners"):
        a("\n**Aucun schéma applicatif détecté.** Renseigner `owners` dans `config.toml`.\n")
        return "\n".join(p)

    a("\n## 1. Volume et périmètre\n")
    a("_Décide : le coût de l'étape `annotate` (un appel LLM par colonne à nommer)._\n")
    a(
        _table(
            ["Schéma", "Tables", "Partitionnées", "Lignes (est.)", "Colonnes"],
            [
                [
                    t["owner"],
                    t["n_tables"],
                    t["n_partitioned"],
                    t["n_rows_est"],
                    next((c["n_columns"] for c in r["columns"] if c["owner"] == t["owner"]), None),
                ]
                for t in r["tables"]
            ],
        )
    )

    a("\n## 2. Commentaires natifs (`COMMENT ON`)\n")
    a(
        "_Décide : l'ordre de grandeur du chantier. Un taux élevé signifie que la\n"
        "sémantique est déjà écrite et qu'il reste surtout à la collecter._\n"
    )
    rows = []
    for c in r["col_comments"]:
        tc = next((x for x in r["tab_comments"] if x["owner"] == c["owner"]), None)
        rows.append(
            [
                c["owner"],
                c["n_columns"],
                c["n_commented"],
                _pct(c["n_commented"], c["n_columns"]),
                _pct(tc["n_commented"], tc["n_objects"]) if tc else "—",
            ]
        )
    a(_table(["Schéma", "Colonnes", "Commentées", "Taux", "Tables commentées"], rows))

    a("\n## 3. Contraintes déclarées\n")
    a(
        "_Décide : faut-il reconstruire le graphe de jointures par test d'inclusion\n"
        "(§2.4) ? Peu de `R` (clés étrangères) est le cas attendu sur un datamart._\n"
    )
    a(
        _table(
            ["Schéma", "Type", "Statut", "Validée", "Nombre"],
            [
                [c["owner"], c["constraint_type"], c["status"], c["validated"], c["n"]]
                for c in r["constraints"]
            ],
        )
    )
    a("\nTypes : `P` primaire · `R` étrangère · `U` unique · `C` contrôle / NOT NULL\n")

    a("\n## 4. Statistiques de l'optimiseur\n")
    a(
        "_Décide : profiling gratuit (§2.3) ou scans `GROUP BY` à écrire et cadencer.\n"
        "Les histogrammes portent sur les colonnes de faible cardinalité — donc sur\n"
        "les colonnes de code, celles dont on veut le dictionnaire de valeurs._\n"
    )
    a(
        _table(
            [
                "Schéma",
                "Colonnes",
                "Analysées",
                "Taux",
                "Histogr.",
                "Plus ancienne",
                "Plus récente",
            ],
            [
                [
                    s["owner"],
                    s["n_columns"],
                    s["n_analyzed"],
                    _pct(s["n_analyzed"], s["n_columns"]),
                    s["n_histograms"],
                    s["oldest"],
                    s["newest"],
                ]
                for s in r["stats"]
            ],
        )
    )

    b = r.get("blind_spot") or {}
    a("\n## 5. Où vit la transformation\n")
    a(
        "_Décide : la taille de l'angle mort (§5.3). Une table qu'aucun objet de la\n"
        "base ne référence est alimentée depuis l'extérieur — donc hors de portée du\n"
        "lineage SQL._\n"
    )
    a(
        _table(
            ["Mesure", "Tables", "Part"],
            [
                [
                    "Écrites par du PL/SQL en base",
                    b.get("written_in_db"),
                    _pct(b.get("written_in_db"), b.get("n_tables")),
                ],
                [
                    "Seulement lues (vues) — alimentation externe probable",
                    b.get("read_only"),
                    _pct(b.get("read_only"), b.get("n_tables")),
                ],
                [
                    "Référencées par rien — **angle mort**",
                    b.get("orphan"),
                    _pct(b.get("orphan"), b.get("n_tables")),
                ],
            ],
        )
    )
    syn = r.get("synonyms")
    a(f"\nSynonymes à résoudre : **{syn if syn is not None else '—'}**\n")

    pt = r.get("parse") or {}
    a("\n## 6. Test de parsing `sqlglot` — le résultat décisif\n")
    a(
        "_Décide : la viabilité du lineage colonne à colonne (§2.2), c'est-à-dire de\n"
        "la meilleure source de sens du projet._\n"
    )
    a(
        f"\n- Vues : **{pt.get('n_views', 0)}** · vues matérialisées : "
        f"**{pt.get('n_mviews') if pt.get('n_mviews') is not None else '—'}**\n"
        f"- Échantillon testé : **{pt.get('tested', 0)}** (réparti sur la plage de tailles)\n"
        f"- Parsées : **{pt.get('ok', 0)} / {pt.get('tested', 0)}** "
        f"({_pct(pt.get('ok'), pt.get('tested'))})\n"
        f"- Tables extraites de l'arbre : **{pt.get('tables_extracted', 0)}**\n"
    )
    a(
        "\nLecture : au-dessus de ~85 %, le lineage est viable tel quel. En dessous de\n"
        "~50 %, prévoir une extraction dégradée par expressions régulières sur la part\n"
        "non parsable, et le marquer explicitement comme moins fiable.\n"
    )
    if pt.get("failures"):
        a("\n### Échecs de parsing\n")
        a(
            _table(
                ["Vue", "Taille", "Erreur"],
                [[f["view"], f["text_length"], f["error"]] for f in pt["failures"]],
            )
        )

    a("\n## 7. Exposition aux consommateurs\n")
    a(
        "_Décide : le classement de centralité qui remplace les logs de requêtes\n"
        "(§2.5). Les bénéficiaires d'un `SELECT` massif sont les rôles de restitution._\n"
    )
    a(
        _table(
            ["Bénéficiaire", "Objets en lecture"],
            [[g["grantee"], g["n_objects"]] for g in r.get("grants", [])],
        )
    )

    a(f"\n## 8. Tables les plus référencées (top {cfg.top_n})\n")
    a("_Décide : le périmètre prioritaire à documenter et à faire valider._\n")
    a(
        _table(
            ["Table", "Objets dépendants"],
            [
                [f"{t['referenced_owner']}.{t['referenced_name']}", t["n_dependents"]]
                for t in r.get("top_referenced", [])
            ],
        )
    )

    if r.get("errors"):
        a("\n## 9. Ce qui n'a pas pu être lu\n")
        a("_Un privilège manquant est un résultat : il faut le demander ou s'en passer._\n")
        a(_table(["Requête", "Erreur"], [[k, v] for k, v in r["errors"].items()]))

    return "\n".join(p)
