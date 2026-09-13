#!/usr/bin/env python3
"""
Version script du notebook d'exploration `coverage_omd.ipynb` : mesure la
couverture des metadonnees OMD pour un service (defaut DMT_INT) et fige le
resultat dans build/coverage/. Lecture seule (aucun appel PATCH/PUT).

A la difference de `coverage.py` (pense pour `python -m src.eval.coverage`),
ce script se lance directement, sans -m : `python src/eval/main.py`.

Usage:
    python src/eval/main.py [--service DMT_INT]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from src.eval.metrics import CoverageResult, accumulate  # noqa: E402
from src.eval.omd_client import OMDReadOnlyClient  # noqa: E402
from src.eval.report import dump_json, print_report  # noqa: E402


def main() -> None:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--service", default="DMT_INT", help="Service/datamart OMD a auditer")
    args = parser.parse_args()

    client = OMDReadOnlyClient()

    # Recupere les tables (pagination geree par le SDK) pour inspection avant calcul.
    tables = list(client.list_tables(args.service))
    print(f"{len(tables)} table(s) trouvee(s) : {[t.name.root for t in tables[:5]]}")

    # Calcule les metriques de couverture (une requete de lineage par table).
    result = CoverageResult(service=args.service)
    for table in tables:
        lineage = client.lineage_flags(table)
        accumulate(result, table, lineage)

    print_report(result)

    # Fige le resultat pour comparer la progression semaine apres semaine.
    path = dump_json(result, ROOT / "build" / "coverage")
    print(f"Resultats bruts ecrits dans {path}")


if __name__ == "__main__":
    main()
