from datetime import datetime

from docmaker.models import (
    Catalog,
    CatalogColumn,
    CatalogTable,
    JoinCandidate,
    Joins,
    PriorityEntry,
    PriorityReport,
    PrioritySignals,
)


def _catalog() -> Catalog:
    table = CatalogTable(
        fqn="OWNER.T",
        owner="OWNER",
        name="T",
        columns=[CatalogColumn(name="ID", position=1, data_type="NUMBER", nullable=False)],
    )
    return Catalog(generated_at=datetime.now(), owners=["OWNER"], tables=[table])


def test_catalog_has_column_is_case_insensitive():
    catalog = _catalog()
    assert catalog.has_column("OWNER.T", "id")
    assert catalog.has_column("OWNER.T", "ID")
    assert not catalog.has_column("OWNER.T", "OTHER")


def test_catalog_all_table_fqns_includes_tables_views_and_mviews():
    catalog = _catalog()
    assert catalog.all_table_fqns() == {"OWNER.T"}


def test_join_candidate_cardinality_matrix():
    def cand(left_unique, right_unique):
        return JoinCandidate(
            left="A.X",
            right="B.Y",
            evidence="declared_fk",
            left_unique=left_unique,
            right_unique=right_unique,
        )

    assert cand(True, True).cardinality == "1:1"
    assert cand(True, False).cardinality == "1:N"
    assert cand(False, True).cardinality == "N:1"
    assert cand(False, False).cardinality == "N:N"
    assert cand(None, True).cardinality == "?"


def test_joins_verified_edges_only_includes_confirmed_inclusion():
    joins = Joins(
        generated_at=datetime.now(),
        candidates=[
            JoinCandidate(left="A.X", right="B.Y", evidence="declared_fk", included=True),
            JoinCandidate(left="C.X", right="D.Y", evidence="name_match", included=False),
            JoinCandidate(left="E.X", right="F.Y", evidence="observed_lineage", included=None),
        ],
    )
    assert joins.verified_edges() == {frozenset(("A.X", "B.Y"))}


def test_priority_report_top_respects_existing_order():
    report = PriorityReport(
        generated_at=datetime.now(),
        entries=[
            PriorityEntry(fqn="A", signals=PrioritySignals(), score=0.9),
            PriorityEntry(fqn="B", signals=PrioritySignals(), score=0.5),
            PriorityEntry(fqn="C", signals=PrioritySignals(), score=0.1),
        ],
    )
    assert report.top(2) == ["A", "B"]
