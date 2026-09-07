from docmaker.pipeline.conflicts import field_conflict


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
