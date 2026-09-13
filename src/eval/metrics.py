"""Calcul des metriques de couverture de metadonnees a partir des tables OMD."""

from __future__ import annotations

from dataclasses import dataclass

from metadata.generated.schema.entity.data.storedProcedure import StoredProcedure
from metadata.generated.schema.entity.data.table import Table

from .omd_client import LineageFlags


def _text(markdown) -> str:
    """Table.description / Column.description sont des `Markdown` (root=str)."""
    return markdown.root.strip() if markdown else ""


@dataclass
class CoverageResult:
    service: str
    n_tables: int = 0
    n_columns: int = 0
    n_procedures: int = 0
    tables_described: int = 0
    tables_owned: int = 0
    tables_tagged: int = 0
    columns_described: int = 0
    columns_tagged: int = 0
    tables_with_full_lineage: int = 0
    procedures_described: int = 0

    @staticmethod
    def _pct(part: int, total: int) -> float:
        return round(100 * part / total, 1) if total else 0.0

    @property
    def pct_tables_described(self) -> float:
        return self._pct(self.tables_described, self.n_tables)

    @property
    def pct_tables_owned(self) -> float:
        return self._pct(self.tables_owned, self.n_tables)

    @property
    def pct_tables_tagged(self) -> float:
        return self._pct(self.tables_tagged, self.n_tables)

    @property
    def pct_columns_described(self) -> float:
        return self._pct(self.columns_described, self.n_columns)

    @property
    def pct_columns_tagged(self) -> float:
        return self._pct(self.columns_tagged, self.n_columns)

    @property
    def pct_tables_full_lineage(self) -> float:
        return self._pct(self.tables_with_full_lineage, self.n_tables)

    @property
    def pct_procedures_described(self) -> float:
        return self._pct(self.procedures_described, self.n_procedures)


def accumulate(result: CoverageResult, table: Table, lineage: LineageFlags) -> None:
    """Met a jour `result` en place avec une table et son statut de lineage."""
    result.n_tables += 1
    if _text(table.description):
        result.tables_described += 1
    if table.owners and table.owners.root:
        result.tables_owned += 1
    if table.tags:  # tag de classification ou terme de glossaire : meme champ
        result.tables_tagged += 1
    if lineage.has_upstream and lineage.has_downstream:
        result.tables_with_full_lineage += 1
    for column in table.columns:
        result.n_columns += 1
        if _text(column.description):
            result.columns_described += 1
        if column.tags:
            result.columns_tagged += 1


def accumulate_procedure(result: CoverageResult, procedure: StoredProcedure) -> None:
    """Met a jour `result` en place avec une procedure stockee (description uniquement)."""
    result.n_procedures += 1
    if _text(procedure.description):
        result.procedures_described += 1
