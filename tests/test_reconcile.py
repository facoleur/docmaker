from docmaker.models import Column, Facts, NoteFacts, SourceRef, TableFacts
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


def _note(table, text, ref):
    return NoteFacts(table=table, text=text, source_refs=[SourceRef(file=ref, locator="X")])


def test_identical_notes_merge_and_union_sources():
    facts = Facts(
        notes=[
            _note("Account", "Le solde ne peut être négatif.", "a.docx"),
            _note("account", "le solde ne peut  être négatif.", "b.xlsx"),
            _note("Account", "Clôture après 90 jours d'inactivité.", "c.pdf"),
        ]
    )
    model = reconcile(facts)
    assert len(model.notes) == 2
    merged = next(n for n in model.notes if "négatif" in n.text)
    assert {r.file for r in merged.source_refs} == {"a.docx", "b.xlsx"}


def test_nullable_conflict_keeps_its_provenance():
    """§7.4 : `nullable` passait à côté de field_conflict et perdait sa source."""
    facts = Facts(
        tables=[
            _table("Account", "balance", "a.docx", nullable=False),
            _table("Account", "balance", "b.xlsx", nullable=True),
        ]
    )
    model = reconcile(facts)
    conflict = next(c for c in model.conflicts if c.field == "nullable")
    assert all(v["source"] for v in conflict.values)


def test_divergence_is_carried_by_the_merged_column():
    """§7.1 : le tableau doit pouvoir afficher la divergence, pas un arbitrage."""
    facts = Facts(
        tables=[
            _table("Account", "balance", "a.docx", type="DECIMAL(18,2)"),
            _table("Account", "balance", "b.xlsx", type="VARCHAR(20)"),
        ]
    )
    col = reconcile(facts).entities[0].columns[0]
    assert set(col.conflicts["type"]) == {"DECIMAL(18,2)", "VARCHAR(20)"}


def test_type_formatting_noise_does_not_create_a_conflict():
    """§7.3 : sans normalisation, ce bruit noierait les vraies divergences."""
    facts = Facts(
        tables=[
            _table("Account", "balance", "a.docx", type="DECIMAL(18, 2)"),
            _table("Account", "balance", "b.xlsx", type="decimal(18,2)"),
        ]
    )
    model = reconcile(facts)
    assert model.conflicts == []
    assert model.entities[0].columns[0].conflicts == {}
