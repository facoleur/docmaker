"""Enrichissement de descriptions via un LLM - batch offline UNIQUEMENT. Ne
jamais appeler ce module depuis le pipeline d'ingestion OMD : il tourne a la
demande / sur planification separee, et n'ecrit jamais vers OMD lui-meme (seul
src/sink/omd.py le fait).

Le choix et l'appel du LLM (Ollama local ou OpenRouter) sont geres par
`LLMClient` dans src/enrich/llm.py - voir son docstring pour la contrainte de
confidentialite entre les deux modes. Ce module ne fait qu'assembler le
contexte disponible (DDL/colonnes depuis OMD, description et tags existants,
conventions minees par src/sources/conventions.py) et parser la sortie JSON.
"""

from __future__ import annotations

import argparse
import itertools
import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from metadata.generated.schema.entity.data.table import Table

from src.enrich.llm import LLMClient
from src.eval.omd_client import client_from_env
from src.proposal import Proposal, SourceType
from src.sources.conventions import mine_conventions

logger = logging.getLogger(__name__)

# Charge au niveau module (pas dans main()) : LLMClient lit ses variables d'env
# a l'instanciation, avant qu'un eventuel appel a load_dotenv() dans main() n'ait joue.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _context(table: Table, column_name: str | None, conventions: list[str]) -> str:
    lines = [f"Table: {table.fullyQualifiedName.root}"]
    if table.description:
        lines.append(f"Description existante: {table.description.root}")
    for column in table.columns:
        marker = ">>" if column.name.root == column_name else "-"
        desc = f" -- {column.description.root}" if column.description else ""
        lines.append(f"{marker} {column.name.root} ({column.dataType.value}){desc}")
    if conventions:
        lines.append("Conventions observees dans l'usage:")
        lines += [f"  - {c}" for c in conventions]
    return "\n".join(lines)


def propose_description(
    table: Table,
    column_name: str | None = None,
    conventions: list[str] | None = None,
    llm: LLMClient | None = None,
) -> Proposal:
    """Une table/colonne -> une Proposal LLM.

    `conventions` : sortie de mine_conventions, si disponible.
    """
    llm = llm or LLMClient()
    context = _context(table, column_name, conventions or [])
    target = (
        f"{table.fullyQualifiedName.root}.{column_name}"
        if column_name
        else table.fullyQualifiedName.root
    )
    subject = f"la colonne {column_name}" if column_name else "la table"
    logger.info("proposition pour %s", target)
    prompt = (
        f"Tu es data steward sur un datamart bancaire. A partir du contexte ci-dessous, "
        f"propose une description factuelle et concise (une phrase) pour {subject}. "
        "Reponds uniquement en JSON avec les cles proposed_value, confidence (0-1) "
        "et confidence_reason.\n\n"
        f"{context}"
    )
    data = llm.generate(prompt)
    proposed = data["proposed_value"].strip()
    return Proposal(
        entity_fqn=target,
        entity_type="column" if column_name else "table",
        target_field="description",
        proposed_value=proposed,
        source_type=SourceType.llm_generated,
        source_ref=llm.model_name,
        confidence=float(data.get("confidence", 0.5)),
        confidence_reason=data.get("confidence_reason"),
        source_hash=Proposal.compute_hash(proposed),
    )


def run_batch(
    service: str, table_fqn: str | None = None, limit: int | None = None
) -> list[Proposal]:
    """`table_fqn` : ne traite que cette table (test rapide, ignore `service`/`limit` pour la
    selection et saute le minage de conventions - inutile pour une seule table, et couteux
    puisqu'il boucle sur tout le service). `limit` : plafonne le nombre de tables traitees.
    """
    llm = LLMClient()
    client = client_from_env()

    if table_fqn:
        table = client.get_by_name(entity=Table, fqn=table_fqn, fields=["tags", "columns"])
        if table is None:
            raise SystemExit(f"table introuvable dans OMD: {table_fqn}")
        tables: Iterable[Table] = [table]
        conventions_by_column: dict[str, list[str]] = {}
    else:
        mined, _ = mine_conventions(service)
        conventions_by_column = defaultdict(list)
        for p in mined:
            conventions_by_column[p.entity_fqn].append(p.proposed_value)
        tables = client.list_all_entities(
            entity=Table, fields=["tags", "columns"], params={"service": service}
        )
        if limit:
            tables = itertools.islice(tables, limit)

    proposals = []
    for table in tables:
        table_fqn_ = table.fullyQualifiedName.root
        if not table.description:
            proposals.append(propose_description(table, llm=llm))
        for column in table.columns:
            if column.description:
                continue
            column_fqn = f"{table_fqn_}.{column.name.root}"
            proposals.append(
                propose_description(
                    table, column.name.root, conventions_by_column.get(column_fqn), llm=llm
                )
            )
    return proposals


def main() -> None:
    # force=True : une dependance (SDK OMD) configure deja un handler sur le root logger a
    # l'import, ce qui rendrait un basicConfig() normal sans effet (niveau WARNING silencieux).
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", force=True
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True, help="Service/datamart OMD a enrichir")
    parser.add_argument(
        "--table",
        help=(
            "Ne teste qu'une seule table (FQN OMD complete, ex: "
            "'banking db.default.dmt.DMT_CPT_ACT_M') ; ignore --service pour la selection"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Plafonne le nombre de tables traitees (ignore si --table est utilise)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Affiche les propositions sans les sauvegarder"
    )
    args = parser.parse_args()

    proposals = run_batch(args.service, table_fqn=args.table, limit=args.limit)

    if args.dry_run:
        for p in proposals:
            print(p.model_dump_json(indent=2))
        return

    out_dir = Path(__file__).resolve().parents[2] / "build" / "proposals"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"describe_{args.service}_{stamp}.jsonl"
    path.write_text("\n".join(p.model_dump_json() for p in proposals), encoding="utf-8")
    print(
        f"{len(proposals)} proposition(s) ecrite(s) dans {path} "
        "(a rejouer via src.run vers le sink)"
    )


if __name__ == "__main__":
    main()
