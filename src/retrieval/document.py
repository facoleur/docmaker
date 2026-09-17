"""Composition des documents indexables a partir du catalogue OMD.

Deux regles structurent le module :

1. **Identite et sens sont embeddes separement.** `identity_text` porte les
   identifiants deplies, `meaning_text` la description et les termes de glossaire.
   Un vecteur unique moyennerait les deux : ajouter une description a une table
   bien nommee fait alors *baisser* son score, ce qui est absurde. Avec deux
   vecteurs (voir index.py, qui prend le max des deux similarites), une
   description qui repond a la question fait gagner l'entite, et une description
   hors sujet ne peut plus penaliser un bon match de nom.
2. **Ce qui sert a restreindre reste structure.** Schema, type d'entite et tags de
   classification ne sont pas embeddes : un tag `PII.Sensitive` dilue le vecteur
   sans aider la similarite, alors qu'en filtre il repond exactement a "les
   colonnes sensibles de ce schema".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.retrieval.abbreviations import expand_identifier
from src.retrieval.catalog import Catalog, GlossaryEntry, split_tags


@dataclass
class Document:
    """Unite indexable : une table ou une colonne."""

    fqn: str
    entity_type: str  # "table" | "column"
    name: str
    schema: str
    identity_text: str  # identifiants deplies
    meaning_text: str = ""  # description + glossaire ; vide tant que rien n'est documente
    tags: list[str] = field(default_factory=list)  # classification uniquement
    has_description: bool = False

    @property
    def has_meaning(self) -> bool:
        return bool(self.meaning_text.strip())

    def to_dict(self) -> dict:
        return {
            "fqn": self.fqn,
            "entity_type": self.entity_type,
            "name": self.name,
            "schema": self.schema,
            "identity_text": self.identity_text,
            "meaning_text": self.meaning_text,
            "tags": self.tags,
            "has_description": self.has_description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Document:
        return cls(**data)


def _glossary_text(fqns: list[str], glossary: dict[str, GlossaryEntry]) -> str:
    """Termes de glossaire references, deplies en libelle + synonymes + definition.
    C'est le seul endroit ou un mot du metier entre dans l'index sans avoir ete
    ecrit dans la description de l'entite elle-meme.
    """
    parts = [glossary[fqn].as_text() for fqn in fqns if fqn in glossary]
    return " ; ".join(parts)


def _join(*parts: str) -> str:
    return "\n".join(p.strip() for p in parts if p and p.strip())


def build_documents(catalog: Catalog, include_columns: bool = True) -> list[Document]:
    """Construit un document par table (et par colonne si demande).

    La fiche de table agrege les noms de ses colonnes : c'est la granularite utile
    au text-to-SQL ("quelle table repond a cette question ?"). La fiche de colonne
    sert les questions qui visent un champ precis.
    """
    synonyms = catalog.synonym_map()
    documents: list[Document] = []

    for table in catalog.tables:
        table_fqn = table.fullyQualifiedName.root
        table_name = table.name.root
        schema = table.databaseSchema.name if table.databaseSchema else ""
        description = table.description.root if table.description else ""
        glossary_fqns, classification_fqns = split_tags(table.tags)
        columns = table.columns or []

        documents.append(
            Document(
                fqn=table_fqn,
                entity_type="table",
                name=table_name,
                schema=schema,
                identity_text=_join(
                    f"table {expand_identifier(table_name, synonyms)}",
                    "colonnes : "
                    + ", ".join(expand_identifier(c.name.root, synonyms) for c in columns),
                ),
                meaning_text=_join(
                    description, _glossary_text(glossary_fqns, catalog.glossary)
                ),
                tags=classification_fqns,
                has_description=bool(description),
            )
        )

        if not include_columns:
            continue

        for column in columns:
            column_description = column.description.root if column.description else ""
            column_glossary, column_classification = split_tags(column.tags)
            documents.append(
                Document(
                    fqn=column.fullyQualifiedName.root,
                    entity_type="column",
                    name=column.name.root,
                    schema=schema,
                    identity_text=_join(
                        f"colonne {expand_identifier(column.name.root, synonyms)} "
                        f"de la table {expand_identifier(table_name, synonyms)}",
                        f"type {column.dataTypeDisplay or ''}",
                    ),
                    meaning_text=_join(
                        column_description,
                        _glossary_text(column_glossary, catalog.glossary),
                    ),
                    tags=column_classification,
                    has_description=bool(column_description),
                )
            )

    return documents
