from datetime import datetime

from docmaker.models import (
    Catalog,
    CatalogColumn,
    CatalogConstraint,
    CatalogTable,
    JoinEdge,
    Lineage,
    ParseStats,
)
from docmaker.pipeline.joins import build_candidates, declared_fk_pairs, name_match_pairs


def _col(name: str) -> CatalogColumn:
    return CatalogColumn(name=name, position=1, data_type="NUMBER", nullable=True)


def _fact_and_dim():
    dim = CatalogTable(
        fqn="OWNER.DIM_CLI",
        owner="OWNER",
        name="DIM_CLI",
        columns=[_col("CLI_ID")],
        constraints=[CatalogConstraint(name="PK_CLI", type="P", columns=["CLI_ID"])],
    )
    fact = CatalogTable(
        fqn="OWNER.FACT_INC",
        owner="OWNER",
        name="FACT_INC",
        columns=[_col("CLI_ID")],
        constraints=[
            CatalogConstraint(
                name="FK_CLI",
                type="R",
                columns=["CLI_ID"],
                r_constraint_fqn="OWNER.DIM_CLI",
                r_columns=["CLI_ID"],
            )
        ],
    )
    return fact, dim


def test_declared_fk_pairs_extracted_from_constraints():
    fact, dim = _fact_and_dim()
    catalog = Catalog(generated_at=datetime.now(), owners=["OWNER"], tables=[fact, dim])
    pairs = declared_fk_pairs(catalog)
    assert pairs == [("OWNER.FACT_INC.CLI_ID", "OWNER.DIM_CLI.CLI_ID")]


def test_build_candidates_dedupes_across_sources_keeping_declared_fk():
    fact, dim = _fact_and_dim()
    catalog = Catalog(generated_at=datetime.now(), owners=["OWNER"], tables=[fact, dim])
    lineage = Lineage(
        generated_at=datetime.now(),
        joins=[JoinEdge(left="OWNER.FACT_INC.CLI_ID", right="OWNER.DIM_CLI.CLI_ID", frequency=3)],
        parse_stats=ParseStats(n_objects=1, n_parsed=1, n_failed=0),
    )
    scope = {"OWNER.FACT_INC", "OWNER.DIM_CLI"}
    candidates = build_candidates(catalog, lineage, priority_scope=scope)
    assert len(candidates) == 1  # même paire vue par declared_fk et lineage : une seule candidate
    assert candidates[0].evidence == "declared_fk"


def test_name_match_pairs_restricted_to_priority_scope():
    fact, dim = _fact_and_dim()
    other = CatalogTable(fqn="OWNER.OTHER", owner="OWNER", name="OTHER", columns=[_col("CLI_ID")])
    catalog = Catalog(generated_at=datetime.now(), owners=["OWNER"], tables=[fact, dim, other])
    pairs = name_match_pairs(catalog, priority_scope={"OWNER.FACT_INC", "OWNER.DIM_CLI"})
    tables_seen = {p for pair in pairs for p in pair}
    assert not any("OTHER" in t for t in tables_seen)
