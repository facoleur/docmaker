"""Modele Proposal : sortie commune de toutes les sources/enrich, seule entree du sink.

Aucun module de sources/ ou enrich/ n'ecrit vers OMD : chacun ne fait que produire
des Proposal. Seul sink/omd.py a le droit d'ecrire (voir son docstring).
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

TargetField = Literal["description", "tag", "glossaryTerm"]


class SourceType(str, Enum):
    oracle_dictionary = "oracle_dictionary"
    sql_parsing = "sql_parsing"
    tableau_twb = "tableau_twb"
    llm_generated = "llm_generated"


class Proposal(BaseModel):
    entity_fqn: str
    entity_type: str
    target_field: TargetField
    proposed_value: str
    source_type: SourceType
    source_ref: str
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_reason: str | None = None
    source_hash: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    human_touched: bool = False
    status: str = "draft"

    @staticmethod
    def compute_hash(content: str) -> str:
        """Sha256 tronque de `content` ; meme recette pour toutes les sources,
        pour que sink/omd.py puisse comparer deux propositions par leur contenu.
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
