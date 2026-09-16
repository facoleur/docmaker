"""Schéma et (dé)sérialisation de `semantic/model.yaml` — docs/poc-qualite-service.md
étape 5. Voir l'exemple commenté dans le corps du document pour la forme cible.

`model.yaml` est **généré puis amendé par la revue** : une entité marquée
`reviewed: true` a été validée par un humain et ne doit plus jamais être
écrasée par une régénération automatique — `save_model` applique cette règle.
C'est l'équivalent, côté fichier plat, de la règle "human_touched" de
`src/sink/omd.py`.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class DimensionDef(BaseModel):
    expr: str
    values: dict[str, str] = Field(default_factory=dict)  # code -> libellé


class MeasureDef(BaseModel):
    expr: str


class Entity(BaseModel):
    root: str
    satellites: list[str] = Field(default_factory=list)
    grain: str = ""
    key: str = ""
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    default_filters: list[str] = Field(default_factory=list)
    dimensions: dict[str, DimensionDef] = Field(default_factory=dict)
    measures: dict[str, MeasureDef] = Field(default_factory=dict)
    # Une fois à true, l'entrée est protégée : `save_model` ne la remplace plus.
    reviewed: bool = False


class SemanticModel(BaseModel):
    entities: dict[str, Entity] = Field(default_factory=dict)


def load_model(path: Path) -> SemanticModel:
    if not path.exists():
        return SemanticModel()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SemanticModel.model_validate(raw)


def save_model(model: SemanticModel, path: Path, *, merge: bool = True) -> SemanticModel:
    """Écrit `model` en YAML déterministe (clés triées). Si `merge`, les entités
    déjà `reviewed` dans le fichier existant sont préservées telles quelles,
    quelle que soit la proposition régénérée pour le même nom.
    """
    final = model
    if merge and path.exists():
        existing = load_model(path)
        reviewed = {name: e for name, e in existing.entities.items() if e.reviewed}
        final = SemanticModel(entities={**model.entities, **reviewed})

    entities = {name: e.model_dump(exclude_none=True) for name, e in sorted(final.entities.items())}
    payload = {"entities": entities}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=True, default_flow_style=False),
        encoding="utf-8",
    )
    return final
