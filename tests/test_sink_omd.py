"""Tests des 4 regles de src/sink/omd.py, avec un client OMD mocke (aucun reseau)."""
import uuid
from unittest.mock import MagicMock

import pytest
from metadata.generated.schema.entity.data.table import Column, Table
from metadata.generated.schema.type.basic import EntityExtension

from src.proposal import Proposal, SourceType
from src.sink.omd import write_batch, write_proposal

BOT_USER = "enrichment-bot"


@pytest.fixture(autouse=True)
def _bot_env(monkeypatch):
    monkeypatch.setenv("OMD_BOT_USER", BOT_USER)


def make_table(*, updated_by=BOT_USER, extension=None, with_column=True) -> Table:
    columns = [Column(name="COL", dataType="VARCHAR", fullyQualifiedName="svc.db.sch.T.COL")] if with_column else []
    return Table(
        id=str(uuid.uuid4()),
        name="T",
        fullyQualifiedName="svc.db.sch.T",
        updatedBy=updated_by,
        columns=columns,
        extension=EntityExtension(extension) if extension is not None else None,
    )


def make_proposal(**overrides) -> Proposal:
    fields = {
        "entity_fqn": "svc.db.sch.T",
        "entity_type": "table",
        "target_field": "description",
        "proposed_value": "Table des contrats actifs.",
        "source_type": SourceType.sql_parsing,
        "source_ref": "test",
        "confidence": 0.9,
        "source_hash": Proposal.compute_hash("Table des contrats actifs."),
    }
    fields.update(overrides)
    return Proposal(**fields)


def fake_client(table: Table | None) -> MagicMock:
    client = MagicMock()
    client.get_by_name.return_value = table
    client.patch.return_value = table
    return client


def test_skip_unsupported_entity_type():
    client = fake_client(None)
    ok, reason = write_proposal(make_proposal(entity_type="chart"), client=client)
    assert (ok, reason) == (False, "entity_type non supporte: chart")
    client.get_by_name.assert_not_called()


def test_skip_entity_not_found():
    client = fake_client(None)
    ok, reason = write_proposal(make_proposal(), client=client)
    assert (ok, reason) == (False, "entite introuvable")
    client.patch.assert_not_called()


def test_skip_human_touched():
    client = fake_client(make_table(updated_by="alice.dba"))
    ok, reason = write_proposal(make_proposal(), client=client)
    assert (ok, reason) == (False, "human_touched")
    client.patch.assert_not_called()


def test_skip_when_bot_never_touched_it_is_not_treated_as_human():
    """updatedBy=None (jamais touche) ne doit pas etre confondu avec une correction humaine."""
    client = fake_client(make_table(updated_by=None))
    ok, reason = write_proposal(make_proposal(), client=client)
    assert ok is True
    assert reason == "ecrit"


def test_skip_unchanged():
    proposal = make_proposal()
    client = fake_client(make_table(extension={"lastSourceHash": proposal.source_hash}))
    ok, reason = write_proposal(proposal, client=client)
    assert (ok, reason) == (False, "unchanged")
    client.patch.assert_not_called()


def test_writes_table_description_with_review_tag_and_hash():
    proposal = make_proposal()
    client = fake_client(make_table())
    ok, reason = write_proposal(proposal, client=client)
    assert (ok, reason) == (True, "ecrit")

    client.patch.assert_called_once()
    destination = client.patch.call_args.kwargs["destination"]
    assert destination.description.root == proposal.proposed_value
    assert destination.extension.root["lastSourceHash"] == proposal.source_hash
    assert any(t.tagFQN.root == "IA – à valider" for t in destination.tags)
    assert all(t.state.value == "Suggested" for t in destination.tags)


def test_writes_column_description():
    proposal = make_proposal(entity_fqn="svc.db.sch.T.COL", entity_type="column", proposed_value="Statut du compte.")
    proposal = proposal.model_copy(update={"source_hash": Proposal.compute_hash(proposal.proposed_value)})
    client = fake_client(make_table())
    ok, reason = write_proposal(proposal, client=client)
    assert (ok, reason) == (True, "ecrit")

    destination = client.patch.call_args.kwargs["destination"]
    assert destination.columns[0].description.root == "Statut du compte."
    assert destination.description is None  # la table elle-meme n'est pas touchee


def test_skip_column_not_found():
    proposal = make_proposal(entity_fqn="svc.db.sch.T.INEXISTANTE", entity_type="column")
    client = fake_client(make_table())
    ok, reason = write_proposal(proposal, client=client)
    assert (ok, reason) == (False, "colonne introuvable")
    client.patch.assert_not_called()


def test_glossary_term_uses_glossary_source():
    proposal = make_proposal(target_field="glossaryTerm", proposed_value="Glossary.Contrat")
    proposal = proposal.model_copy(update={"source_hash": Proposal.compute_hash(proposal.proposed_value)})
    client = fake_client(make_table())
    write_proposal(proposal, client=client)

    destination = client.patch.call_args.kwargs["destination"]
    tag = next(t for t in destination.tags if t.tagFQN.root == "Glossary.Contrat")
    assert tag.source.value == "Glossary"
    assert tag.state.value == "Suggested"


def test_write_batch_summary():
    written_table = make_table()
    skipped_table = make_table(updated_by="alice.dba")

    def get_by_name(entity, fqn, fields=None):  # noqa: ARG001
        return written_table if fqn == "svc.db.sch.T" else skipped_table

    client = MagicMock()
    client.get_by_name.side_effect = get_by_name
    client.patch.return_value = written_table

    proposals = [
        make_proposal(entity_fqn="svc.db.sch.T"),
        make_proposal(entity_fqn="svc.db.sch.OTHER"),
        make_proposal(entity_type="chart"),
    ]
    summary = write_batch(proposals, client=client)
    assert summary["written"] == 1
    assert summary["errors"] == 0
    assert summary["skipped"] == {"human_touched": 1, "entity_type non supporte: chart": 1}
