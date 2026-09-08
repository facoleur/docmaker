"""reconcile : fusionne les mentions par entité et remonte les divergences.

Fusion par nom normalisé (tables et colonnes). Conflits détectés en v1 sur les
champs structurants des colonnes : type, nullable, key.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from ..config import Settings
from ..models import Conflict, DocModel, Entity, Facts, MergedColumn, NoteFacts, SourceRef
from .conflicts import field_conflict

log = logging.getLogger(__name__)


def run(settings: Settings) -> None:
    facts = Facts.model_validate_json((settings.build_dir / "facts.json").read_text("utf-8"))
    model = reconcile(facts)
    (settings.build_dir / "model.json").write_text(model.model_dump_json(indent=2), encoding="utf-8")
    log.info(
        "%d entité(s), %d relation(s), %d note(s), %d conflit(s)",
        len(model.entities),
        len(model.relations),
        len(model.notes),
        len(model.conflicts),
    )


def reconcile(facts: Facts) -> DocModel:
    by_table: dict[str, list] = defaultdict(list)
    for table in facts.tables:
        by_table[_norm(table.name)].append(table)

    entities: list[Entity] = []
    conflicts: list[Conflict] = []
    for key in sorted(by_table):
        group = by_table[key]
        name = group[0].name

        cols: dict[str, list] = defaultdict(list)
        for table in group:
            for col in table.columns:
                cols[_norm(col.name)].append((col, table.source_refs))

        merged: list[MergedColumn] = []
        for ckey in sorted(cols):
            observed = cols[ckey]
            cname = observed[0][0].name
            entity_field = f"{name}.{cname}"

            for field in ("type", "key"):
                conflict = field_conflict(
                    entity_field,
                    field,
                    [(getattr(col, field), _src(refs)) for col, refs in observed],
                )
                if conflict:
                    conflicts.append(conflict)
            nulls = {col.nullable for col, _ in observed if col.nullable is not None}
            if len(nulls) > 1:
                conflicts.append(
                    Conflict(
                        entity=entity_field,
                        field="nullable",
                        values=[{"value": str(n), "source": ""} for n in sorted(map(str, nulls))],
                    )
                )

            merged.append(
                MergedColumn(
                    name=cname,
                    type=_first(col.type for col, _ in observed),
                    nullable=next(iter(nulls)) if len(nulls) == 1 else None,
                    key=_first(col.key for col, _ in observed),
                    description=_first(col.description for col, _ in observed),
                    source_refs=_dedup(r for _, refs in observed for r in refs),
                )
            )

        entities.append(
            Entity(
                name=name,
                description=_first(t.description for t in group),
                columns=merged,
                source_refs=_dedup(r for t in group for r in t.source_refs),
            )
        )

    return DocModel(
        generated_at=datetime.now(),
        entities=entities,
        relations=facts.relations,
        notes=_dedup_notes(facts.notes),
        conflicts=conflicts,
    )


def _dedup_notes(notes: Iterable[NoteFacts]) -> list[NoteFacts]:
    """Fusionne les notes au texte identique (par table), en unissant leurs sources."""
    out: dict[tuple[str, str], NoteFacts] = {}
    for n in notes:
        key = (_norm(n.table), re.sub(r"\s+", " ", n.text.strip().casefold()))
        if key in out:
            out[key].source_refs = _dedup([*out[key].source_refs, *n.source_refs])
        else:
            out[key] = n.model_copy(deep=True)
    return list(out.values())


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def _src(refs: list[SourceRef]) -> str:
    return "; ".join(f"{r.file}#{r.locator}" for r in refs) or "?"


def _first(values: Iterable) -> str:
    for v in values:
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _dedup(refs: Iterable[SourceRef]) -> list[SourceRef]:
    seen: set[tuple[str, str]] = set()
    out: list[SourceRef] = []
    for r in refs:
        k = (r.file, r.locator)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out
