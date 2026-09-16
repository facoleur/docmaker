"""Tests de `src/sources/omd_feedback.py`, sans réseau (aucun client OMD réel)."""

import uuid

from metadata.generated.schema.entity.data.table import Column, Table
from metadata.generated.schema.type.basic import EntityExtension

from src.proposal import Proposal
from src.sources.omd_feedback import compute_feedback

BOT_USER = "enrichment-bot"


def make_table(
    *, description="Description proposée par le bot.", updated_by=BOT_USER, with_hash=True
) -> Table:
    column = Column(
        name="COL",
        dataType="VARCHAR",
        fullyQualifiedName="svc.db.sch.T.COL",
        description="Description de colonne, proposée par le bot.",
    )
    extension = EntityExtension({"lastSourceHash": "abc123"}) if with_hash else None
    return Table(
        id=str(uuid.uuid4()),
        name="T",
        fullyQualifiedName="svc.db.sch.T",
        updatedBy=updated_by,
        description=description,
        columns=[column],
        extension=extension,
    )


def test_table_without_last_source_hash_is_ignored():
    """N'est jamais entrée dans le pipeline (pas de lastSourceHash) : rien à suivre."""
    pivot, events = compute_feedback({}, [make_table(with_hash=False)], BOT_USER)
    assert pivot == {}
    assert events == []


def test_first_sighting_bootstraps_pivot_without_events():
    pivot, events = compute_feedback({}, [make_table()], BOT_USER)
    assert events == []
    entry = pivot["svc.db.sch.T::description"]
    assert entry["proposed_value"] == "Description proposée par le bot."
    assert entry["human_touched"] is False
    assert entry["confidence"] == 0.5


def test_untouched_by_human_produces_no_change():
    pivot, _ = compute_feedback({}, [make_table()], BOT_USER)
    pivot2, events = compute_feedback(pivot, [make_table()], BOT_USER)
    assert events == []
    assert pivot2 == pivot


def test_confirmed_without_text_change_raises_confidence():
    text = "Description proposée par le bot."
    pivot = {
        "svc.db.sch.T::description": {
            "proposed_value": text,
            "source_hash": Proposal.compute_hash(text),
            "confidence": 0.5,
            "human_touched": False,
        }
    }
    table = make_table(description=text, updated_by="alice.dba")
    updated, events = compute_feedback(pivot, [table], BOT_USER)
    assert events == []
    entry = updated["svc.db.sch.T::description"]
    assert entry["confidence"] == 1.0
    assert entry["human_touched"] is True


def test_corrected_text_is_logged_as_a_feedback_event():
    old_text = "Ancienne proposition du bot."
    pivot = {
        "svc.db.sch.T::description": {
            "proposed_value": old_text,
            "source_hash": Proposal.compute_hash(old_text),
            "confidence": 0.5,
            "human_touched": False,
        }
    }
    table = make_table(description="Correction humaine du texte.", updated_by="alice.dba")
    updated, events = compute_feedback(pivot, [table], BOT_USER)

    assert len(events) == 1
    event = events[0]
    assert event["entity_fqn"] == "svc.db.sch.T"
    assert event["proposed"] == old_text
    assert event["corrected"] == "Correction humaine du texte."

    entry = updated["svc.db.sch.T::description"]
    assert entry["proposed_value"] == "Correction humaine du texte."
    assert entry["human_touched"] is True
    assert entry["confidence"] == 1.0


def test_column_feedback_is_tracked_independently_from_table():
    pivot, _ = compute_feedback({}, [make_table()], BOT_USER)
    entry = pivot.get("svc.db.sch.T.COL::description")
    assert entry is not None
    assert entry["proposed_value"] == "Description de colonne, proposée par le bot."
