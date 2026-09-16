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
    # champ -> valeurs distinctes observées, quand les sources divergent.
    # Le rendu doit afficher la divergence au lieu d'un arbitrage silencieux
    # (`prise-de-recul.md` §7.1). Vide = sources d'accord.
    conflicts: dict[str, list[str]] = Field(default_factory=dict)


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


# --------------------------------------------------------------------------- #
# Étape catalog — POC qualité de service (docs/poc-qualite-service.md étape 1) #
# Fidèle à Oracle, pas au modèle documentaire ci-dessus : voir                #
# couche-semantique.md §6.1 pour la justification de chaque parti pris.       #
# --------------------------------------------------------------------------- #
class CatalogColumn(BaseModel):
    name: str
    position: int
    data_type: str
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    nullable: bool
    default: str | None = None
    key: str = ""  # "PK" | "FK" | "U" | ""
    comment: str = ""


class CatalogIndex(BaseModel):
    name: str
    columns: list[str]
    unique: bool


class CatalogConstraint(BaseModel):
    name: str
    type: str  # "P" | "R" | "U" | "C"
    columns: list[str]
    status: str = ""
    validated: str = ""
    r_constraint_fqn: str | None = None  # table cible résolue, uniquement pour "R"
    r_columns: list[str] = Field(default_factory=list)  # colonnes cibles, même ordre que `columns`


class CatalogTable(BaseModel):
    fqn: str  # "OWNER.NAME" — identité utilisée par toutes les étapes suivantes
    owner: str
    name: str
    comment: str = ""
    partitioned: bool = False
    num_rows: int | None = None
    last_analyzed: datetime | None = None
    columns: list[CatalogColumn] = Field(default_factory=list)
    indexes: list[CatalogIndex] = Field(default_factory=list)
    constraints: list[CatalogConstraint] = Field(default_factory=list)


class CatalogView(BaseModel):
    fqn: str
    owner: str
    name: str
    comment: str = ""
    columns: list[CatalogColumn] = Field(default_factory=list)
    sql: str | None = None


class CatalogMView(BaseModel):
    fqn: str
    owner: str
    name: str
    comment: str = ""
    columns: list[CatalogColumn] = Field(default_factory=list)
    sql: str | None = None


class CatalogSynonym(BaseModel):
    fqn: str  # OWNER.NAME du synonyme lui-même
    target_fqn: str  # OWNER.NAME résolu


class CatalogDependency(BaseModel):
    referenced_fqn: str
    dependent_fqn: str
    dependent_type: str


class CatalogProcedure(BaseModel):
    fqn: str
    type: str  # "PROCEDURE" | "FUNCTION" | "PACKAGE" | "PACKAGE BODY" | "TRIGGER"
    n_lines: int


class Catalog(BaseModel):
    generated_at: datetime
    owners: list[str]
    tables: list[CatalogTable] = Field(default_factory=list)
    views: list[CatalogView] = Field(default_factory=list)
    mviews: list[CatalogMView] = Field(default_factory=list)
    synonyms: list[CatalogSynonym] = Field(default_factory=list)
    dependencies: list[CatalogDependency] = Field(default_factory=list)
    procedures: list[CatalogProcedure] = Field(default_factory=list)

    def all_table_fqns(self) -> set[str]:
        return {t.fqn for t in self.tables} | {v.fqn for v in self.views} | {
            m.fqn for m in self.mviews
        }

    def columns_of(self, fqn: str) -> list[CatalogColumn]:
        for holder in (*self.tables, *self.views, *self.mviews):
            if holder.fqn == fqn:
                return holder.columns
        return []

    def has_column(self, table_fqn: str, column_name: str) -> bool:
        return any(c.name.upper() == column_name.upper() for c in self.columns_of(table_fqn))


# --------------------------------------------------------------------------- #
# Étape priority — docs/poc-qualite-service.md étape 2                        #
# --------------------------------------------------------------------------- #
class PrioritySignals(BaseModel):
    """Un signal manquant reste `None` — jamais mis à zéro (fausserait le rang)."""

    exposure: int | None = None  # nb de rôles/comptes ayant SELECT (ALL_TAB_PRIVS)
    activity: int | None = None  # inserts+updates+deletes (ALL_TAB_MODIFICATIONS)
    fanout: int | None = None  # nb d'objets dépendants (ALL_DEPENDENCIES)
    volume: int | None = None  # NUM_ROWS
    freshness_days: float | None = None  # jours depuis LAST_ANALYZED (plus petit = plus frais)


class PriorityEntry(BaseModel):
    fqn: str
    signals: PrioritySignals
    score: float  # moyenne des rangs percentiles des signaux disponibles, dans [0, 1]


class PriorityReport(BaseModel):
    generated_at: datetime
    entries: list[PriorityEntry] = Field(default_factory=list)  # triées par score décroissant

    def top(self, n: int) -> list[str]:
        return [e.fqn for e in self.entries[:n]]


# --------------------------------------------------------------------------- #
# Étape lineage — docs/poc-qualite-service.md étape 3                         #
# --------------------------------------------------------------------------- #
class ColumnLineage(BaseModel):
    target: str  # "OWNER.VIEW.COLONNE"
    expression: str
    sources: list[str] = Field(default_factory=list)  # "OWNER.TABLE.COLONNE" amont, résolues
    object_fqn: str  # la vue/MV qui porte l'expression


class JoinEdge(BaseModel):
    left: str  # "OWNER.TABLE.COLONNE"
    right: str
    frequency: int
    sample_objects: list[str] = Field(default_factory=list)


class DeadColumn(BaseModel):
    fqn: str  # "OWNER.TABLE.COLONNE" — déclarée au DDL, jamais référencée dans le SQL parsé


class ParseStats(BaseModel):
    n_objects: int
    n_parsed: int
    n_failed: int
    failures: list[str] = Field(default_factory=list)  # FQN des objets non parsés

    @property
    def unparsed_ratio(self) -> float:
        return 1 - (self.n_parsed / self.n_objects) if self.n_objects else 0.0


class Lineage(BaseModel):
    generated_at: datetime
    columns: list[ColumnLineage] = Field(default_factory=list)
    joins: list[JoinEdge] = Field(default_factory=list)
    dead_columns: list[DeadColumn] = Field(default_factory=list)
    parse_stats: ParseStats


# --------------------------------------------------------------------------- #
# Étape profile — docs/poc-qualite-service.md étape 4                         #
# --------------------------------------------------------------------------- #
class TopValue(BaseModel):
    value: str
    frequency: float  # part du total, dans [0, 1]


class ValueProfile(BaseModel):
    column: str  # "OWNER.TABLE.COLONNE"
    source: str  # "stats" | "histogram" | "scan"
    n_distinct: int | None = None
    n_nulls: int | None = None
    top_values: list[TopValue] = Field(default_factory=list)
    last_analyzed: datetime | None = None


class GrainResult(BaseModel):
    table: str
    key_columns: list[str]
    is_grain: bool | None  # None = non vérifiable (pas de clé candidate)
    evidence: str  # "declared_pk" | "unique_index" | "aucune"


class FreshnessResult(BaseModel):
    table: str
    load_date_column: str
    max_value: datetime | str | None


class Profile(BaseModel):
    generated_at: datetime
    values: list[ValueProfile] = Field(default_factory=list)
    grain: list[GrainResult] = Field(default_factory=list)
    freshness: list[FreshnessResult] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Étape joins — docs/poc-qualite-service.md étape 4                           #
# --------------------------------------------------------------------------- #
class JoinCandidate(BaseModel):
    left: str  # "OWNER.TABLE.COLONNE"
    right: str
    evidence: str  # "declared_fk" | "observed_lineage" | "name_match"
    included: bool | None = None  # None = test d'inclusion non exécuté
    left_unique: bool | None = None
    right_unique: bool | None = None

    @property
    def cardinality(self) -> str:
        if self.left_unique is None or self.right_unique is None:
            return "?"
        return {
            (True, True): "1:1",
            (True, False): "1:N",
            (False, True): "N:1",
            (False, False): "N:N",
        }[(self.left_unique, self.right_unique)]


class Joins(BaseModel):
    generated_at: datetime
    candidates: list[JoinCandidate] = Field(default_factory=list)

    def verified_edges(self) -> set[frozenset[str]]:
        """Arêtes confirmées par le test d'inclusion, pour le validateur (étape 7)."""
        return {
            frozenset((c.left, c.right)) for c in self.candidates if c.included
        }


# --------------------------------------------------------------------------- #
# Étape infer — docs/poc-qualite-service.md étape 5 (build/entities.json,     #
# les candidats bruts avant nommage LLM — semantic/model.yaml est le livrable)#
# --------------------------------------------------------------------------- #
class EntityCandidate(BaseModel):
    root: str
    role: str  # "fact" | "dimension" | "objet" (repli, cf. risques : clustering dégradé)
    satellites: list[str] = Field(default_factory=list)
    key: str = ""
    is_grain: bool | None = None
    confidence: float
    evidence: list[str] = Field(default_factory=list)


class EntityCandidates(BaseModel):
    generated_at: datetime
    candidates: list[EntityCandidate] = Field(default_factory=list)
