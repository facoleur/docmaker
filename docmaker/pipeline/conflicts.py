"""Détection de conflits — module pur : aucune I/O, aucun appel LLM.

Un conflit = au moins deux valeurs distinctes non vides pour un même champ d'une
même entité, observées dans des sources différentes.
"""

from __future__ import annotations

from ..models import Conflict


def field_conflict(entity: str, field: str, observations: list[tuple[str, str]]) -> Conflict | None:
    """`observations` : liste de (valeur, source). Rend un Conflict ou None."""
    groups: dict[str, list[dict]] = {}
    for value, source in observations:
        v = (value or "").strip()
        if not v:
            continue
        groups.setdefault(v.casefold(), []).append({"value": v, "source": source})
    if len(groups) <= 1:
        return None
    return Conflict(
        entity=entity,
        field=field,
        values=[{**g[0], "count": len(g)} for g in groups.values()],
        severity="warn",
    )
