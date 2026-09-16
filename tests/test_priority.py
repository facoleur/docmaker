from datetime import datetime, timedelta

from docmaker.models import Catalog, CatalogTable
from docmaker.pipeline.priority import _percentile_ranks, build_report


def _table(name: str, *, num_rows=None, last_analyzed=None) -> CatalogTable:
    return CatalogTable(
        fqn=f"OWNER.{name}",
        owner="OWNER",
        name=name,
        num_rows=num_rows,
        last_analyzed=last_analyzed,
    )


def test_percentile_ranks_higher_is_better():
    ranks = _percentile_ranks({"a": 1, "b": 2, "c": 3})
    assert ranks["a"] == 0.0
    assert ranks["b"] == 0.5
    assert ranks["c"] == 1.0


def test_percentile_ranks_lower_is_better_inverts():
    ranks = _percentile_ranks({"a": 1, "b": 2, "c": 3}, higher_is_better=False)
    assert ranks["a"] == 1.0
    assert ranks["c"] == 0.0


def test_percentile_ranks_missing_values_absent_from_result():
    ranks = _percentile_ranks({"a": 1, "b": None, "c": 3})
    assert "b" not in ranks
    assert set(ranks) == {"a", "c"}


def test_percentile_ranks_single_value_gets_top_rank():
    assert _percentile_ranks({"a": 42}) == {"a": 1.0}


def test_build_report_orders_by_score_descending():
    now = datetime.now()
    catalog = Catalog(
        generated_at=now,
        owners=["OWNER"],
        tables=[
            _table("HOT", num_rows=1_000_000, last_analyzed=now),
            _table("COLD", num_rows=10, last_analyzed=now - timedelta(days=400)),
        ],
    )
    report = build_report(
        catalog,
        exposure={"OWNER.HOT": 5},
        activity={"OWNER.HOT": 1000},
        fanout={"OWNER.HOT": 20, "OWNER.COLD": 1},
    )
    assert report.entries[0].fqn == "OWNER.HOT"
    assert report.entries[0].score > report.entries[1].score
    assert report.top(1) == ["OWNER.HOT"]


def test_build_report_missing_signals_not_zeroed():
    """Une table sans aucun signal disponible ne doit pas décrocher à zéro si
    elle est la seule sans exposition/activité — le score reste basé sur ce qui
    est mesurable (volume, fraîcheur), pas pénalisé pour un signal absent.
    """
    now = datetime.now()
    catalog = Catalog(
        generated_at=now,
        owners=["OWNER"],
        tables=[_table("ONLY", num_rows=100, last_analyzed=now)],
    )
    report = build_report(catalog, exposure={}, activity={}, fanout={})
    assert report.entries[0].score == 1.0  # seul signal disponible (volume, fraîcheur) => rang max
