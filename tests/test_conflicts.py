from docmaker.pipeline.conflicts import field_conflict, normalize_type


def test_no_conflict_when_single_distinct_value():
    obs = [("INT", "a.docx"), ("int", "b.xlsx"), ("", "c.pdf")]
    assert field_conflict("t.c", "type", obs) is None


def test_conflict_detected_with_sources():
    conflict = field_conflict("t.c", "type", [("INT", "a.docx"), ("VARCHAR", "b.xlsx")])
    assert conflict is not None
    assert {v["value"] for v in conflict.values} == {"INT", "VARCHAR"}
    assert {v["source"] for v in conflict.values} == {"a.docx", "b.xlsx"}


def test_empty_values_are_ignored():
    assert field_conflict("t.c", "key", [("", "a"), ("  ", "b")]) is None


def test_formatting_noise_is_not_a_conflict():
    """VARCHAR(20) / varchar (20) / VARCHAR (20) : une seule valeur, pas trois."""
    obs = [("VARCHAR(20)", "a.docx"), ("varchar (20)", "b.xlsx"), ("VARCHAR ( 20 )", "c.pdf")]
    assert field_conflict("t.c", "type", obs, normalize=normalize_type) is None


def test_type_aliases_are_resolved():
    obs = [("INT", "a.docx"), ("INTEGER", "b.xlsx")]
    assert field_conflict("t.c", "type", obs, normalize=normalize_type) is None


def test_real_type_divergence_survives_normalization():
    obs = [("DECIMAL(18,2)", "a.docx"), ("VARCHAR(20)", "b.xlsx")]
    assert field_conflict("t.c", "type", obs, normalize=normalize_type) is not None


def test_oracle_number_is_not_conflated_with_decimal():
    """Conflater les deux masquerait une différence de sémantique réelle."""
    assert normalize_type("NUMBER") != normalize_type("DECIMAL")


def test_displayed_value_stays_verbatim():
    """On normalise pour comparer, jamais pour réécrire ce qu'une source dit."""
    conflict = field_conflict(
        "t.c", "type", [("decimal(18,2)", "a.docx"), ("VARCHAR(20)", "b.xlsx")],
        normalize=normalize_type,
    )
    assert conflict is not None
    assert "decimal(18,2)" in {v["value"] for v in conflict.values}
