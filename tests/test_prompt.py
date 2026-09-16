from datetime import datetime

from docmaker.models import JoinCandidate, Joins
from docmaker.semantic.model import Entity, MeasureDef, SemanticModel
from docmaker.semantic.prompt import (
    build_context,
    estimate_tokens,
    neighbors,
    select_entities,
    summary,
)


def _model() -> SemanticModel:
    return SemanticModel(
        entities={
            "incident": Entity(
                root="DMT.DMT_F_QOS_INC",
                satellites=["DMT.DMT_F_QOS_INC_DET"],
                grain="un incident déclaré",
                measures={"nb_incidents": MeasureDef(expr="COUNT(*)")},
            ),
            "client": Entity(root="DMT.DIM_CLI", grain="un client"),
        }
    )


def test_summary_lists_every_entity_with_its_measures():
    text = summary(_model())
    assert "incident" in text
    assert "nb_incidents" in text
    assert "client" in text


def test_select_entities_matches_on_grain_tokens():
    # Le matcher est lexical (pas de lemmatisation) : le mot doit apparaître
    # sous la même forme que dans le nom/la maille de l'entité (singulier ici).
    selected = select_entities("quelle est la maille de l'entité incident ?", _model())
    assert selected[0] == "incident"


def test_select_entities_empty_question_returns_nothing():
    assert select_entities("", _model()) == []


def _join(evidence: str, included: bool) -> JoinCandidate:
    return JoinCandidate(
        left="DMT.DMT_F_QOS_INC.CLI_ID",
        right="DMT.DIM_CLI.CLI_ID",
        evidence=evidence,
        included=included,
    )


def test_neighbors_follows_verified_join_edges():
    model = _model()
    joins = Joins(generated_at=datetime.now(), candidates=[_join("declared_fk", True)])
    assert neighbors(model, "incident", joins) == ["client"]


def test_neighbors_ignores_unverified_joins():
    model = _model()
    joins = Joins(generated_at=datetime.now(), candidates=[_join("name_match", False)])
    assert neighbors(model, "incident", joins) == []


def test_build_context_falls_back_to_summary_when_nothing_matches():
    no_joins = Joins(generated_at=datetime.now())
    ctx = build_context("question hors sujet xyz", _model(), no_joins, max_tokens=1000)
    assert ctx.level == 1
    assert ctx.selected_entities == []


def test_build_context_includes_detail_when_budget_allows():
    no_joins = Joins(generated_at=datetime.now())
    ctx = build_context("parle-moi de l'entité incident", _model(), no_joins, max_tokens=100_000)
    assert ctx.level == 2
    assert "incident" in ctx.selected_entities


def test_estimate_tokens_is_positive_and_roughly_proportional():
    assert estimate_tokens("a" * 400) > estimate_tokens("a" * 40)
