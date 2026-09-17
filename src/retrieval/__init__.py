"""Recherche semantique sur le catalogue OMD (lecture seule).

    from src.retrieval import build, search

    build(service_name="banking db", schemas={"dmt", "ods", "ref", "stg", "tec"})
    for hit in search("solde journalier d'un compte", entity_type="table"):
        print(hit.score, hit.document.fqn)
"""

from __future__ import annotations

from src.retrieval.catalog import load_catalog
from src.retrieval.document import build_documents
from src.retrieval.index import SearchHit, SemanticIndex

__all__ = ["build", "search", "SemanticIndex", "SearchHit"]


def build(
    service_name: str, schemas: set[str] | None = None, include_columns: bool = True
) -> SemanticIndex:
    """Relit le catalogue OMD, reconstruit l'index et le persiste dans build/retrieval/."""
    documents = build_documents(load_catalog(service_name, schemas), include_columns)
    index = SemanticIndex.build(documents)
    index.save()
    return index


def search(question: str, **filters) -> list[SearchHit]:
    """Recherche dans l'index persiste (a construire au prealable avec build())."""
    return SemanticIndex.load().search(question, **filters)
