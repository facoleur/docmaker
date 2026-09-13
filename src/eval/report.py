"""Rendu du rapport de couverture : tableau rich (ou print simple) + dump JSON horodate."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .metrics import CoverageResult

try:
    from rich.console import Console
    from rich.table import Table as RichTable
except ImportError:
    RichTable = None

# (libelle, attribut %, attribut compte, attribut total)
ROWS = [
    ("Tables avec description", "pct_tables_described", "tables_described", "n_tables"),
    ("Tables avec owner", "pct_tables_owned", "tables_owned", "n_tables"),
    ("Tables avec tag/glossaire", "pct_tables_tagged", "tables_tagged", "n_tables"),
    (
        "Tables avec lineage amont ET aval",
        "pct_tables_full_lineage",
        "tables_with_full_lineage",
        "n_tables",
    ),
    ("Colonnes avec description", "pct_columns_described", "columns_described", "n_columns"),
    ("Colonnes avec tag/glossaire", "pct_columns_tagged", "columns_tagged", "n_columns"),
    (
        "Procedures stockees avec description",
        "pct_procedures_described",
        "procedures_described",
        "n_procedures",
    ),
]


def print_report(result: CoverageResult) -> None:
    if RichTable is not None:
        table = RichTable(title=f"Couverture des metadonnees - service {result.service}")
        table.add_column("Metrique")
        table.add_column("Couverture", justify="right")
        table.add_column("Detail", justify="right")
        for label, pct_attr, part_attr, total_attr in ROWS:
            table.add_row(
                label,
                f"{getattr(result, pct_attr)}%",
                f"{getattr(result, part_attr)}/{getattr(result, total_attr)}",
            )
        console = Console()
        console.print(table)
        console.print(
            f"{result.n_tables} tables, {result.n_columns} colonnes, "
            f"{result.n_procedures} procedures stockees scannees."
        )
    else:
        print(f"Couverture des metadonnees - service {result.service}")
        for label, pct_attr, part_attr, total_attr in ROWS:
            pct, part, total = (
                getattr(result, pct_attr),
                getattr(result, part_attr),
                getattr(result, total_attr),
            )
            print(f"  {label:<38} {pct:>5}%  ({part}/{total})")
        print(
            f"{result.n_tables} tables, {result.n_columns} colonnes, "
            f"{result.n_procedures} procedures stockees scannees."
        )


def dump_json(result: CoverageResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"coverage_{date.today():%Y%m%d}.json"
    payload = {
        "service": result.service,
        "n_tables": result.n_tables,
        "n_columns": result.n_columns,
        "n_procedures": result.n_procedures,
        "tables_described": result.tables_described,
        "tables_owned": result.tables_owned,
        "tables_tagged": result.tables_tagged,
        "columns_described": result.columns_described,
        "columns_tagged": result.columns_tagged,
        "tables_with_full_lineage": result.tables_with_full_lineage,
        "procedures_described": result.procedures_described,
        "pct_tables_described": result.pct_tables_described,
        "pct_tables_owned": result.pct_tables_owned,
        "pct_tables_tagged": result.pct_tables_tagged,
        "pct_columns_described": result.pct_columns_described,
        "pct_columns_tagged": result.pct_columns_tagged,
        "pct_tables_full_lineage": result.pct_tables_full_lineage,
        "pct_procedures_described": result.pct_procedures_described,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
