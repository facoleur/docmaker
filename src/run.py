#!/usr/bin/env python3
"""
Orchestration nightly : lance les sources choisies, collecte les Proposal,
les passe au sink OMD (PATCH idempotent, jamais PUT).

`--proposals-file` charge en plus un .jsonl de Proposal deja generees (ex: la
sortie de `src.enrich.describe`, relue et validee par un humain avant d'etre
appliquee) - c'est le maillon qui reste manuel entre l'enrichissement LLM
(volontairement hors pipeline automatique) et l'ecriture reelle vers OMD.

Usage:
    python -m src.run --sources conventions,tableau
    --service DMT_INT [--twb fichier.twb] [--proposals-file f.jsonl] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

from src.eval.omd_client import client_from_env
from src.proposal import Proposal
from src.sink.omd import write_batch
from src.sources.conventions import mine_conventions
from src.sources.tableau_twb import parse_twb

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
KNOWN_SOURCES = ("conventions", "tableau")


def collect(sources: list[str], service: str, twb_path: str | None) -> list[Proposal]:
    proposals: list[Proposal] = []

    if "conventions" in sources:
        mined, unparsable_rate = mine_conventions(service)
        proposals += mined
        print(
            f"conventions: {len(mined)} proposition(s) ({unparsable_rate:.1%} requetes non parsables par SQLGlot)"
        )

    if "tableau" in sources:
        if not twb_path:
            raise SystemExit("--twb est requis pour la source 'tableau'")
        parsed = parse_twb(twb_path)
        proposals += parsed
        print(f"tableau: {len(parsed)} proposition(s)")

    return proposals


def load_proposals_file(path: str) -> list[Proposal]:
    """Relit un .jsonl de Proposal (une par ligne), ex: la sortie de src.enrich.describe."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [Proposal.model_validate_json(line) for line in lines if line.strip()]


def main() -> None:
    load_dotenv(ROOT / ".env")
    # force=True : une dependance (SDK OMD) configure deja un handler sur le root logger a
    # l'import, ce qui rendrait un basicConfig() normal sans effet (niveau WARNING silencieux).
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", force=True)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--sources",
        default="conventions",
        help="Liste separee par des virgules parmi: " + ",".join(KNOWN_SOURCES),
    )
    parser.add_argument("--service", default="DMT_INT", help="Service/datamart OMD cible")
    parser.add_argument("--twb", help="Chemin du fichier .twb (requis pour la source 'tableau')")
    parser.add_argument(
        "--proposals-file",
        help="Fichier .jsonl de Proposal deja generees (ex: sortie de src.enrich.describe) a inclure",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="N'ecrit rien dans OMD ; affiche les propositions"
    )
    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = set(sources) - set(KNOWN_SOURCES)
    if unknown:
        raise SystemExit(f"source(s) inconnue(s): {sorted(unknown)} (connues: {KNOWN_SOURCES})")

    proposals = collect(sources, args.service, args.twb)

    if args.proposals_file:
        loaded = load_proposals_file(args.proposals_file)
        proposals += loaded
        print(f"fichier: {len(loaded)} proposition(s) chargee(s) depuis {args.proposals_file}")

    print(f"{len(proposals)} proposition(s) collectee(s) au total")

    if args.dry_run:
        for p in proposals:
            print(p.model_dump_json())
        return

    summary = write_batch(proposals, client=client_from_env())
    print(
        f"ecrits: {summary['written']}, erreurs: {summary['errors']}, skips: {summary['skipped']}"
    )


if __name__ == "__main__":
    main()
