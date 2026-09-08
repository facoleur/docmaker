"""Schémas partagés par toutes les étapes. Chaque étape (dé)sérialise en JSON dans build/."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Provenance                                                                  #
# --------------------------------------------------------------------------- #
class SourceRef(BaseModel):
    file: str
    locator: str = ""  # chemin de titre, nom de feuille, page, slide…


# --------------------------------------------------------------------------- #
# Étape ingest                                                                #
# --------------------------------------------------------------------------- #
class SourceDoc(BaseModel):
    path: str
    fmt: str
    sha256: str
    status: str  # "converted" | "error"
    md_path: str | None = None
    note: str = ""
    image_descriptions: int = 0
    unhandled_assets: int = 0


class Manifest(BaseModel):
    generated_at: datetime
    docs: list[SourceDoc]


# --------------------------------------------------------------------------- #
# Étape chunk                                                                 #
# --------------------------------------------------------------------------- #
class Chunk(BaseModel):
    doc: str
    heading_path: str
    text: str
    start: int
    end: int


class ChunkSet(BaseModel):
    chunks: list[Chunk]


# --------------------------------------------------------------------------- #
# Étape extract — `FactSet` est le schéma imposé au LLM (volontairement plat) #
# --------------------------------------------------------------------------- #
class Column(BaseModel):
    name: str
    type: str = ""
    nullable: bool | None = None
    key: str = ""  # "PK" | "FK" | "" …
    description: str = ""


class Table(BaseModel):
    name: str
    description: str = ""
    columns: list[Column] = Field(default_factory=list)


class Relation(BaseModel):
    from_table: str
    to_table: str
    kind: str = ""
    cardinality: str = ""
    description: str = ""


class Note(BaseModel):
    """Fait utile hors du modèle de tables (règle, cycle de vie, rétention, glossaire…)."""

    table: str = ""  # nom de la table concernée, "" si transverse
    topic: str = ""  # mot-clé court, libre
    text: str


class FactSet(BaseModel):
    tables: list[Table] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)


class TableFacts(Table):
    source_refs: list[SourceRef] = Field(default_factory=list)


class RelationFacts(Relation):
    source_refs: list[SourceRef] = Field(default_factory=list)


class NoteFacts(Note):
    source_refs: list[SourceRef] = Field(default_factory=list)


class Facts(BaseModel):
    tables: list[TableFacts] = Field(default_factory=list)
    relations: list[RelationFacts] = Field(default_factory=list)
    notes: list[NoteFacts] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Étape reconcile                                                             #
# --------------------------------------------------------------------------- #
class Conflict(BaseModel):
    entity: str
    field: str
    values: list[dict] = Field(default_factory=list)  # [{"value": str, "source": str, ...}]
    severity: str = "warn"


class MergedColumn(Column):
    source_refs: list[SourceRef] = Field(default_factory=list)


class Entity(BaseModel):
    name: str
    description: str = ""
    columns: list[MergedColumn] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)


class DocModel(BaseModel):
    generated_at: datetime
    entities: list[Entity] = Field(default_factory=list)
    relations: list[RelationFacts] = Field(default_factory=list)
    notes: list[NoteFacts] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
