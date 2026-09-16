"""Aides Oracle partagées par les étapes en lecture seule (`recon`, `catalog`,
`priority`, `profile`, `joins`). Un seul endroit qui sait construire une clause
`IN` paramétrée et exécuter une requête en avalant/consignant les erreurs de
privilège — voir le docstring de `recon.py` pour la justification de cette
tolérance.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)


class Db:
    """Connexion Oracle en lecture seule, tolérante aux privilèges manquants.

    Une vue `ALL_*` inaccessible n'interrompt pas la passe : elle est consignée
    comme un manque dans `errors` — c'est en soi un résultat.
    """

    def __init__(self, conn) -> None:
        self._conn = conn
        self.errors: dict[str, str] = {}

    def rows(
        self,
        label: str,
        sql: str,
        binds: dict | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """`timeout_ms` : borne l'exécution côté serveur (étape 7, exécution en
        lecture seule du validateur — jamais de requête sans borne de temps).
        """
        try:
            if timeout_ms is not None:
                self._conn.call_timeout = timeout_ms
            with self._conn.cursor() as cur:
                cur.execute(sql, binds or {})
                cols = [d[0].lower() for d in cur.description]
                return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001 — on veut consigner, pas planter
            msg = str(exc).splitlines()[0][:200]
            log.warning("requête %s indisponible : %s", label, msg)
            self.errors[label] = msg
            return []
        finally:
            if timeout_ms is not None:
                self._conn.call_timeout = 0

    def scalar(self, label: str, sql: str, binds: dict | None = None):
        rows = self.rows(label, sql, binds)
        return next(iter(rows[0].values())) if rows else None


def in_clause(values: list[str], prefix: str) -> tuple[str, dict[str, str]]:
    """Construit `(:p0, :p1, …)` et les binds associés (Oracle refuse une liste)."""
    names = [f"{prefix}{i}" for i in range(len(values))]
    return "(" + ", ".join(f":{n}" for n in names) + ")", dict(zip(names, values, strict=True))


@contextmanager
def connect(cfg, password: str) -> Iterator[Db]:
    """Ouvre une connexion thin `python-oracledb` et la ferme systématiquement."""
    try:
        import oracledb
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("dépendance manquante : `poetry add oracledb`") from exc

    if not password:
        raise RuntimeError("ORACLE_PASSWORD absent de l'environnement (ou du .env)")

    log.info("connexion à %s (mode thin) …", cfg.dsn)
    conn = oracledb.connect(user=cfg.user, password=password, dsn=cfg.dsn)
    try:
        yield Db(conn)
    finally:
        conn.close()
