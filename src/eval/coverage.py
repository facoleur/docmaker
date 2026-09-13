#!/usr/bin/env python3
"""
Mesure la couverture actuelle des metadonnees OMD pour un service/datamart
(defaut DMT_INT) : % descriptions et % owner/tag au niveau table, % descriptions
et % tags/glossaire au niveau colonne, % tables avec lineage amont ET aval,
% procedures stockees avec description.

Lecture seule (GET uniquement) - config via OMD_HOST_PORT / OMD_JWT_TOKEN.

Usage:
    python -m src.eval.coverage [--service DMT_INT] [--json]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from .metrics import CoverageResult, accumulate, accumulate_procedure
from .omd_client import OMDReadOnlyClient
from .report import dump_json, print_report

ROOT = Path(__file__).resolve().parents[2]


def run(service: str, dump_raw: bool) -> CoverageResult:
    client = OMDReadOnlyClient()
    result = CoverageResult(service=service)

    for table in client.list_tables(service):
        lineage = client.lineage_flags(table)
        accumulate(result, table, lineage)

    for procedure in client.list_stored_procedures(service):
        accumulate_procedure(result, procedure)

    print_report(result)
    if dump_raw:
        path = dump_json(result, ROOT / "build" / "coverage")
        print(f"Resultats bruts ecrits dans {path}")
    return result


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--service", default="DMT_INT", help="Nom du service/datamart OMD a auditer"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="dump_raw",
        help="Dumper aussi les resultats bruts dans build/coverage/coverage_YYYYMMDD.json",
    )
    args = parser.parse_args()
    run(args.service, args.dump_raw)


if __name__ == "__main__":
    main()
