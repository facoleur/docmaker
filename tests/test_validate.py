from datetime import datetime

from docmaker.models import Catalog, CatalogColumn, CatalogTable, JoinCandidate, Joins
from docmaker.semantic.validate import validate_sql


def _catalog() -> Catalog:
    dim = CatalogTable(
        fqn="OWNER.DIM_CLI",
        owner="OWNER",
        name="DIM_CLI",
        columns=[CatalogColumn(name="CLI_ID", position=1, data_type="NUMBER", nullable=False)],
    )
    fact = CatalogTable(
        fqn="OWNER.FACT_INC",
        owner="OWNER",
        name="FACT_INC",
        columns=[
            CatalogColumn(name="ID_INC", position=1, data_type="NUMBER", nullable=False),
            CatalogColumn(name="CLI_ID", position=2, data_type="NUMBER", nullable=True),
        ],
    )
    return Catalog(generated_at=datetime.now(), owners=["OWNER"], tables=[dim, fact])


def _no_joins() -> Joins:
    return Joins(generated_at=datetime.now())


def test_validate_sql_rejects_unparseable_sql():
    result = validate_sql("SELECT FROM WHERE", _catalog(), _no_joins())
    assert result.ok is False
    assert result.issues[0].rule == "parse"


def test_validate_sql_rejects_unknown_table():
    result = validate_sql("SELECT * FROM OWNER.UNKNOWN_TABLE", _catalog(), _no_joins())
    assert result.ok is False
    assert any(i.rule == "table_inconnue" for i in result.issues)


def test_validate_sql_rejects_unknown_column_even_when_table_unqualified():
    """Régression : une colonne non qualifiée dans une requête mono-table doit
    quand même être vérifiée contre le catalogue (auparavant silencieusement
    ignorée faute de qualificateur de table).
    """
    result = validate_sql("SELECT BAD_COL FROM OWNER.DIM_CLI", _catalog(), _no_joins())
    assert result.ok is False
    assert any(i.rule == "colonne_inconnue" for i in result.issues)


def test_validate_sql_accepts_known_table_and_column():
    result = validate_sql("SELECT CLI_ID FROM OWNER.DIM_CLI", _catalog(), _no_joins())
    assert result.ok is True
    assert not result.errors


def test_validate_sql_warns_but_does_not_reject_unverified_join():
    sql = "SELECT * FROM OWNER.FACT_INC f JOIN OWNER.DIM_CLI d ON f.CLI_ID = d.CLI_ID"
    result = validate_sql(sql, _catalog(), _no_joins())
    assert result.ok is True  # un avertissement seul n'empêche pas ok=True
    assert any(i.rule == "jointure_non_verifiee" for i in result.warnings)


def test_validate_sql_no_warning_for_verified_join():
    sql = "SELECT * FROM OWNER.FACT_INC f JOIN OWNER.DIM_CLI d ON f.CLI_ID = d.CLI_ID"
    candidate = JoinCandidate(
        left="OWNER.FACT_INC.CLI_ID",
        right="OWNER.DIM_CLI.CLI_ID",
        evidence="declared_fk",
        included=True,
    )
    joins = Joins(generated_at=datetime.now(), candidates=[candidate])
    result = validate_sql(sql, _catalog(), joins)
    assert not any(i.rule == "jointure_non_verifiee" for i in result.issues)


def test_validate_sql_without_db_warns_explain_not_run():
    result = validate_sql("SELECT CLI_ID FROM OWNER.DIM_CLI", _catalog(), _no_joins(), db=None)
    assert any(i.rule == "explain_non_execute" for i in result.warnings)
