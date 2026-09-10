"""Détection de conflits — module pur : aucune I/O, aucun appel LLM.

Un conflit = au moins deux valeurs distinctes non vides pour un même champ d'une
même entité, observées dans des sources différentes.

Le regroupement passe par une normalisation propre au champ
(`prise-de-recul.md` §7.3) : sans elle, `VARCHAR(20)`, `varchar (20)` et
`VARCHAR (20)` forment trois groupes et fabriquent un faux conflit. Un signal de
conflit auquel on cesse de croire ne vaut rien. La valeur *affichée* reste la
valeur brute rencontrée en premier : on normalise pour comparer, jamais pour
réécrire une source.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ..models import Conflict

# Alias strictement équivalents. Volontairement court : conflater `NUMBER`
# (Oracle) et `DECIMAL` masquerait une différence de sémantique réelle, alors
# qu'on ne cherche ici qu'à absorber le bruit d'écriture.
_TYPE_ALIASES = {
    "INT": "INTEGER",
    "INT4": "INTEGER",
    "INT8": "BIGINT",
    "DEC": "DECIMAL",
    "NUMERIC": "DECIMAL",
    "VARCHAR2": "VARCHAR",
    "CHARACTER VARYING": "VARCHAR",
    "BOOL": "BOOLEAN",
    "TIMESTAMP WITHOUT TIME ZONE": "TIMESTAMP",
}
_SPACES_RE = re.compile(r"\s+")
_PARENS_RE = re.compile(r"\s*\(\s*(.*?)\s*\)")


def normalize_type(value: str) -> str:
    """Casse, espaces et ponctuation de précision absorbés ; alias résolus."""
    v = _SPACES_RE.sub(" ", value.strip().upper())
    v = _PARENS_RE.sub(lambda m: "(" + re.sub(r"\s*,\s*", ",", m.group(1)) + ")", v)
    base, _, precision = v.partition("(")
    base = _TYPE_ALIASES.get(base.strip(), base.strip())
    return base + (("(" + precision) if precision else "")


def field_conflict(
    entity: str,
    field: str,
    observations: list[tuple[str, str]],
    normalize: Callable[[str], str] | None = None,
) -> Conflict | None:
    """`observations` : liste de (valeur, source). Rend un Conflict ou None.

    `normalize` détermine ce qui compte comme « la même valeur » ; par défaut une
    simple insensibilité à la casse.
    """
    key_of = normalize or (lambda v: v.casefold())
    groups: dict[str, list[dict]] = {}
    for value, source in observations:
        v = (value or "").strip()
        if not v:
            continue
        groups.setdefault(key_of(v), []).append({"value": v, "source": source})
    if len(groups) <= 1:
        return None
    return Conflict(
        entity=entity,
        field=field,
        values=[{**g[0], "count": len(g)} for g in groups.values()],
        severity="warn",
    )
