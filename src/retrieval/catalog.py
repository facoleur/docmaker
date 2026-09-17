"""Lecture du catalogue OMD pour la recherche semantique : tables, colonnes,
descriptions, tags et termes de glossaire.

GET uniquement, comme src/eval/omd_client.py. Ce module ne produit pas de
Proposal et n'ecrit jamais : la boucle est `sources/enrich -> sink -> OMD ->
retrieval`, ce qui garantit que sink/omd.py reste le seul point d'ecriture et
que tout enrichissement ameliore mecaniquement la recherche au run suivant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from metadata.generated.schema.entity.data.glossaryTerm import GlossaryTerm
from metadata.generated.schema.entity.data.table import Table
from metadata.generated.schema.type.tagLabel import TagSource

from src.eval.omd_client import client_from_env

logger = logging.getLogger(__name__)

TABLE_FIELDS = ["columns", "tags", "owners"]


@dataclass
class GlossaryEntry:
    """Un terme de glossaire, reduit a ce qui sert au retrieval."""

    fqn: str
    name: str
    description: str = ""
    synonyms: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        """Le terme, ses synonymes et sa definition, prets a etre embeddes."""
        return " ".join(filter(None, [self.name, *self.synonyms, self.description]))


@dataclass
class Catalog:
    """Instantane du catalogue : les tables et le glossaire qu'elles referencent."""

    tables: list[Table]
    glossary: dict[str, GlossaryEntry]

    def synonym_map(self) -> dict[str, str]:
        """Synonymes du glossaire indexes par jeton majuscule (`SLD` -> "solde"),
        pour que expand_identifier fasse primer le vocabulaire gouverne dans OMD
        sur le dictionnaire code en dur.
        """
        mapping: dict[str, str] = {}
        for entry in self.glossary.values():
            for synonym in entry.synonyms:
                token = synonym.strip().upper()
                if token and " " not in token:
                    mapping[token] = entry.name.lower()
        return mapping


def load_glossary(client=None) -> dict[str, GlossaryEntry]:
    """Charge tous les termes de glossaire, indexes par FQN (la cle utilisee par
    les TagLabel de source Glossary posees sur les tables et colonnes).
    """
    client = client or client_from_env()
    entries: dict[str, GlossaryEntry] = {}
    for term in client.list_all_entities(entity=GlossaryTerm, fields=["relatedTerms"]):
        fqn = term.fullyQualifiedName.root if term.fullyQualifiedName else term.name.root
        entries[fqn] = GlossaryEntry(
            fqn=fqn,
            name=(term.displayName or term.name.root),
            description=term.description.root if term.description else "",
            synonyms=[s.root if hasattr(s, "root") else str(s) for s in (term.synonyms or [])],
        )
    logger.info("glossaire OMD : %d terme(s)", len(entries))
    return entries


def load_catalog(service_name: str, schemas: set[str] | None = None, client=None) -> Catalog:
    """Charge les tables d'un service (pagination geree par le SDK), filtrees sur
    les schemas metier si `schemas` est fourni - sans quoi les schemas systeme
    d'Oracle (dvsys, lbacsys, audsys...) noient le corpus.
    """
    client = client or client_from_env()
    tables = []
    for table in client.list_all_entities(
        entity=Table, fields=TABLE_FIELDS, params={"service": service_name}
    ):
        schema = table.databaseSchema.name if table.databaseSchema else ""
        if schemas is None or schema.lower() in schemas:
            tables.append(table)
    logger.info("catalogue OMD : %d table(s) retenue(s)", len(tables))
    return Catalog(tables=tables, glossary=load_glossary(client))


def split_tags(tags) -> tuple[list[str], list[str]]:
    """Separe les TagLabel en (FQN de termes de glossaire, FQN de tags de
    classification). Les deux ne jouent pas le meme role : le glossaire apporte
    du sens (il est embedde), la classification apporte des facettes (elle sert
    de filtre, et diluerait le vecteur si on l'embeddait).
    """
    glossary_fqns, classification_fqns = [], []
    for label in tags or []:
        fqn = label.tagFQN.root if hasattr(label.tagFQN, "root") else str(label.tagFQN)
        if label.source == TagSource.Glossary:
            glossary_fqns.append(fqn)
        else:
            classification_fqns.append(fqn)
    return glossary_fqns, classification_fqns
