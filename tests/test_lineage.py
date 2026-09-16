from datetime import datetime

from docmaker.models import Catalog, CatalogColumn, CatalogTable, CatalogView
from docmaker.pipeline.lineage import build_lineage


def _catalog() -> Catalog:
    dim = CatalogTable(
        fqn="OWNER.DIM_CLI",
        owner="OWNER",
        name="DIM_CLI",
        columns=[
            CatalogColumn(name="CLI_ID", position=1, data_type="NUMBER", nullable=False),
            CatalogColumn(name="LIB_CLI", position=2, data_type="VARCHAR2", nullable=True),
        ],
    )
    fact = CatalogTable(
        fqn="OWNER.FACT_INC",
        owner="OWNER",
        name="FACT_INC",
        columns=[
            CatalogColumn(name="ID_INC", position=1, data_type="NUMBER", nullable=False),
            CatalogColumn(name="CLI_ID", position=2, data_type="NUMBER", nullable=True),
            CatalogColumn(name="MT_MONTANT", position=3, data_type="NUMBER", nullable=True),
        ],
    )
    view = CatalogView(
        fqn="OWNER.V_INC",
        owner="OWNER",
        name="V_INC",
        sql=(
            "SELECT f.ID_INC AS ID_INC, f.CLI_ID AS CLI_ID, f.MT_MONTANT * 1.2 AS MT_MONTANT_TTC "
            "FROM OWNER.FACT_INC f JOIN OWNER.DIM_CLI d ON f.CLI_ID = d.CLI_ID"
        ),
    )
    return Catalog(generated_at=datetime.now(), owners=["OWNER"], tables=[dim, fact], views=[view])


def test_build_lineage_extracts_join_edge():
    lineage = build_lineage(_catalog())
    assert lineage.parse_stats.n_parsed == 1
    assert lineage.parse_stats.n_failed == 0
    pairs = {frozenset((j.left, j.right)) for j in lineage.joins}
    assert frozenset(("OWNER.FACT_INC.CLI_ID", "OWNER.DIM_CLI.CLI_ID")) in pairs


def test_build_lineage_resolves_column_expressions():
    lineage = build_lineage(_catalog())
    by_target = {c.target: c for c in lineage.columns}
    assert "OWNER.V_INC.MT_MONTANT_TTC" in by_target
    assert "OWNER.FACT_INC.MT_MONTANT" in by_target["OWNER.V_INC.MT_MONTANT_TTC"].sources


def test_build_lineage_flags_never_referenced_column_as_dead():
    lineage = build_lineage(_catalog())
    dead_fqns = {d.fqn for d in lineage.dead_columns}
    assert "OWNER.DIM_CLI.LIB_CLI" in dead_fqns
    assert "OWNER.FACT_INC.MT_MONTANT" not in dead_fqns


def test_build_lineage_records_parse_failure_without_crashing():
    catalog = _catalog()
    catalog.views.append(
        CatalogView(fqn="OWNER.V_BROKEN", owner="OWNER", name="V_BROKEN", sql="SELECT FROM WHERE")
    )
    lineage = build_lineage(catalog)
    assert lineage.parse_stats.n_failed == 1
    assert "OWNER.V_BROKEN" in lineage.parse_stats.failures
    assert lineage.parse_stats.unparsed_ratio == 0.5
