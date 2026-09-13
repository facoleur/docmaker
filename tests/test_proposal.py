from src.proposal import Proposal, SourceType


def _make(**overrides) -> Proposal:
    fields = {
        "entity_fqn": "svc.db.schema.T.COL",
        "entity_type": "column",
        "target_field": "description",
        "proposed_value": "une description",
        "source_type": SourceType.sql_parsing,
        "source_ref": "test",
        "confidence": 0.9,
        "source_hash": Proposal.compute_hash("une description"),
    }
    fields.update(overrides)
    return Proposal(**fields)


def test_compute_hash_is_deterministic():
    assert Proposal.compute_hash("abc") == Proposal.compute_hash("abc")


def test_compute_hash_differs_on_content():
    assert Proposal.compute_hash("abc") != Proposal.compute_hash("abd")


def test_compute_hash_is_a_short_hex_string():
    h = Proposal.compute_hash("abc")
    assert len(h) == 16
    int(h, 16)  # ne leve pas si c'est bien de l'hexadecimal


def test_defaults():
    p = _make()
    assert p.human_touched is False
    assert p.status == "draft"
    assert p.confidence_reason is None


def test_confidence_out_of_range_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make(confidence=1.5)
