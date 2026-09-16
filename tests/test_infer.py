from datetime import datetime

from docmaker.models import (
    Catalog,
    CatalogColumn,
    CatalogTable,
    GrainResult,
    JoinCandidate,
    Joins,
    Profile,
)
from docmaker.semantic.infer import cluster


def _table(name: str, columns: list[str], numeric_cols: list[str] | None = None) -> CatalogTable:
    numeric_cols = numeric_cols or []
    cols = [
        CatalogColumn(
            name=c,
            position=i,
            data_type="NUMBER" if c in numeric_cols else "VARCHAR2",
            nullable=True,
        )
        for i, c in enumerate(columns)
    ]
    return CatalogTable(fqn=f"OWNER.{name}", owner="OWNER", name=name, columns=cols)


def _scenario():
    dim_cli = _table("DIM_CLI", ["CLI_ID"])
    fact_inc = _table("FACT_INC", ["ID_INC", "CLI_ID", "MT_MONTANT"], numeric_cols=["MT_MONTANT"])
    fact_ctr = _table("FACT_CTR", ["ID_CTR", "CLI_ID", "MT_MONTANT"], numeric_cols=["MT_MONTANT"])
    fact_inc_det = _table("FACT_INC_DET", ["ID_INC", "LIB_DET"])
    catalog = Catalog(
        generated_at=datetime.now(),
        owners=["OWNER"],
        tables=[dim_cli, fact_inc, fact_ctr, fact_inc_det],
    )

    def _grain(table, key):
        return GrainResult(table=table, key_columns=[key], is_grain=True, evidence="declared_pk")

    profile = Profile(
        generated_at=datetime.now(),
        grain=[
            _grain("OWNER.DIM_CLI", "CLI_ID"),
            _grain("OWNER.FACT_INC", "ID_INC"),
            _grain("OWNER.FACT_CTR", "ID_CTR"),
        ],
    )

    joins = Joins(
        generated_at=datetime.now(),
        candidates=[
            JoinCandidate(
                left="OWNER.FACT_INC.CLI_ID",
                right="OWNER.DIM_CLI.CLI_ID",
                evidence="declared_fk",
                included=True,
                left_unique=False,
                right_unique=True,
            ),
            JoinCandidate(
                left="OWNER.FACT_CTR.CLI_ID",
                right="OWNER.DIM_CLI.CLI_ID",
                evidence="declared_fk",
                included=True,
                left_unique=False,
                right_unique=True,
            ),
            JoinCandidate(
                left="OWNER.FACT_INC.ID_INC",
                right="OWNER.FACT_INC_DET.ID_INC",
                evidence="declared_fk",
                included=True,
                left_unique=True,
                right_unique=False,
            ),
        ],
    )
    scope = {t.fqn for t in catalog.tables}
    return catalog, joins, profile, scope


def test_dimension_referenced_by_two_facts_becomes_its_own_entity():
    catalog, joins, profile, scope = _scenario()
    candidates = cluster(catalog, joins, profile, scope)
    dim = next(c for c in candidates if c.root == "OWNER.DIM_CLI")
    assert dim.role == "dimension"


def test_fact_with_additive_column_and_fine_grain_becomes_fact():
    catalog, joins, profile, scope = _scenario()
    candidates = cluster(catalog, joins, profile, scope)
    fact = next(c for c in candidates if c.root == "OWNER.FACT_INC")
    assert fact.role == "fact"


def test_satellite_with_single_mandatory_join_is_folded_into_its_parent():
    catalog, joins, profile, scope = _scenario()
    candidates = cluster(catalog, joins, profile, scope)
    roots = {c.root for c in candidates}
    assert "OWNER.FACT_INC_DET" not in roots  # jamais sa propre entité
    fact = next(c for c in candidates if c.root == "OWNER.FACT_INC")
    assert "OWNER.FACT_INC_DET" in fact.satellites


def test_cluster_produces_fewer_entities_than_tables():
    catalog, joins, profile, scope = _scenario()
    candidates = cluster(catalog, joins, profile, scope)
    assert len(candidates) < len(catalog.tables)
