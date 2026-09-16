"""Construit l'index depuis OMD puis repond a une question.

Usage:
    python -m src.retrieval --service "banking db" --build
    python -m src.retrieval --service "banking db" -q "solde journalier d'un compte"
"""

from __future__ import annotations

import argparse
import logging

from dotenv import load_dotenv

from src.retrieval import build
from src.retrieval.catalog import load_catalog
from src.retrieval.document import build_documents
from src.retrieval.index import SemanticIndex

DEFAULT_SCHEMAS = {"ref", "stg", "ods", "dmt", "tec"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", default="banking db")
    parser.add_argument("--schemas", default=",".join(sorted(DEFAULT_SCHEMAS)))
    parser.add_argument("--build", action="store_true", help="reconstruit l'index")
    parser.add_argument("-q", "--question")
    parser.add_argument("-k", "--top-k", type=int, default=5)
    parser.add_argument("--entity-type", choices=["table", "column"])
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    schemas = {s.strip().lower() for s in args.schemas.split(",") if s.strip()}

    if args.build:
        index = build(args.service, schemas)
        print(f"index : {len(index.documents)} documents -> build/retrieval/")
    elif not args.question:
        parser.error("--build ou -q est requis")

    if args.question:
        index = SemanticIndex.load()
        documents = build_documents(load_catalog(args.service, schemas))
        if index.is_stale(documents):
            print("! index perime (le catalogue OMD a change) : relancer --build\n")
        for hit in index.search(args.question, top_k=args.top_k, entity_type=args.entity_type):
            document = hit.document
            marque = "+desc" if document.has_description else ""
            print(f"{hit.score:.3f}  {document.entity_type:6} {document.fqn} {marque}")


if __name__ == "__main__":
    main()
