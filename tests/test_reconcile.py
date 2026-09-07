from docmaker.models import Column, Facts, SourceRef, TableFacts
from docmaker.pipeline.reconcile import reconcile


def _table(name, col, ref, **col_kw):
    return TableFacts(
        name=name,
        columns=[Column(name=col, **col_kw)],
        source_refs=[SourceRef(file=ref, locator="X")],
    )


def test_tables_and_columns_merge_by_normalized_name():
    facts = Facts(
        tables=[
            _table("Account", "balance", "a.docx", type="DECIMAL", nullable=False),
            _table("account", "Balance", "b.xlsx", type="DECIMAL", nullable=False),
        ]
    )
    model = reconcile(facts)
    assert len(model.entities) == 1
    assert len(model.entities[0].columns) == 1
    assert len(model.entities[0].columns[0].source_refs) == 2
    assert model.conflicts == []


def test_divergent_type_and_nullable_raise_conflicts():
    facts = Facts(
        tables=[
            _table("Account", "balance", "a.docx", type="DECIMAL", nullable=False),
            _table("Account", "balance", "b.xlsx", type="NUMBER", nullable=True),
        ]
    )
    model = reconcile(facts)
    fields = {(c.entity, c.field) for c in model.conflicts}
    assert ("Account.balance", "type") in fields
    assert ("Account.balance", "nullable") in fields
