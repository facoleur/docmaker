from datetime import datetime

from docmaker.eval.masking import (
    is_refusal,
    mask_comment_eval,
    mask_fk_eval,
    self_consistency_eval,
    similarity,
)
from docmaker.models import (
    Catalog,
    CatalogColumn,
    CatalogConstraint,
    CatalogTable,
    JoinCandidate,
    JoinEdge,
    Joins,
    Lineage,
    ParseStats,
    Profile,
)


def test_similarity_identical_strings_is_one():
    assert similarity("Statut du compte", "statut du compte") == 1.0


def test_similarity_different_strings_is_low():
    assert similarity("Statut du compte", "Montant de la transaction") < 0.5


def test_is_refusal_detects_markers():
    assert is_refusal("Preuve insuffisante pour conclure.")
    assert not is_refusal("Le statut du client actif.")


def _catalog_with_comment() -> Catalog:
    col = CatalogColumn(
        name="STA_CLI", position=1, data_type="VARCHAR2", nullable=True, comment="Statut du client"
    )
    table = CatalogTable(fqn="OWNER.DIM_CLI", owner="OWNER", name="DIM_CLI", columns=[col])
    return Catalog(generated_at=datetime.now(), owners=["OWNER"], tables=[table])


def _empty_lineage() -> Lineage:
    stats = ParseStats(n_objects=0, n_parsed=0, n_failed=0)
    return Lineage(generated_at=datetime.now(), parse_stats=stats)


def test_mask_comment_eval_scores_against_original():
    catalog = _catalog_with_comment()
    profile = Profile(generated_at=datetime.now())

    def annotate(_column_fqn, _evidence):
        return "Statut du client"

    report = mask_comment_eval(
        catalog, _empty_lineage(), profile, annotate, sample_size=10, similarity_threshold=0.5
    )
    assert report.n_sampled == 1
    assert report.accuracy == 1.0
    assert report.items[0].similarity == 1.0


def test_mask_comment_eval_counts_refusals_separately_from_accuracy():
    catalog = _catalog_with_comment()
    profile = Profile(generated_at=datetime.now())

    report = mask_comment_eval(
        catalog,
        _empty_lineage(),
        profile,
        lambda *_: "preuve insuffisante",
        sample_size=10,
        similarity_threshold=0.5,
    )
    assert report.refusal_rate == 1.0
    assert report.accuracy == 0.0  # aucun élément noté (tous refusés), pas "tout faux"


def _fk_setup():
    dim = CatalogTable(
        fqn="OWNER.DIM_CLI",
        owner="OWNER",
        name="DIM_CLI",
        columns=[CatalogColumn(name="CLI_ID", position=1, data_type="NUMBER", nullable=False)],
        constraints=[CatalogConstraint(name="PK_CLI", type="P", columns=["CLI_ID"])],
    )
    fact = CatalogTable(
        fqn="OWNER.FACT_INC",
        owner="OWNER",
        name="FACT_INC",
        columns=[CatalogColumn(name="CLI_ID", position=1, data_type="NUMBER", nullable=True)],
        constraints=[
            CatalogConstraint(
                name="FK_CLI",
                type="R",
                columns=["CLI_ID"],
                r_constraint_fqn="OWNER.DIM_CLI",
                r_columns=["CLI_ID"],
            )
        ],
    )
    catalog = Catalog(generated_at=datetime.now(), owners=["OWNER"], tables=[fact, dim])
    return catalog


def _declared_fk_candidate() -> JoinCandidate:
    return JoinCandidate(
        left="OWNER.FACT_INC.CLI_ID",
        right="OWNER.DIM_CLI.CLI_ID",
        evidence="declared_fk",
        included=True,
    )


def test_mask_fk_eval_fully_rediscovered_when_lineage_and_inclusion_agree():
    catalog = _fk_setup()
    lineage = Lineage(
        generated_at=datetime.now(),
        joins=[JoinEdge(left="OWNER.FACT_INC.CLI_ID", right="OWNER.DIM_CLI.CLI_ID", frequency=1)],
        parse_stats=ParseStats(n_objects=1, n_parsed=1, n_failed=0),
    )
    joins = Joins(generated_at=datetime.now(), candidates=[_declared_fk_candidate()])
    scope = {"OWNER.FACT_INC", "OWNER.DIM_CLI"}
    report = mask_fk_eval(catalog, lineage, joins, priority_scope=scope)
    assert report.n_declared == 1
    assert report.fully_rediscovered == 1
    assert report.missed == []


def test_mask_fk_eval_reports_missed_when_no_independent_evidence():
    catalog = _fk_setup()
    joins = Joins(generated_at=datetime.now(), candidates=[_declared_fk_candidate()])
    # Périmètre prioritaire vide : le rapprochement par nom ne joue pas non plus.
    report = mask_fk_eval(catalog, _empty_lineage(), joins, priority_scope=set())
    assert report.fully_rediscovered == 0
    assert len(report.missed) == 1


def test_self_consistency_eval_flags_diverging_descriptions_as_fragile():
    catalog = _catalog_with_comment()
    profile = Profile(generated_at=datetime.now())

    responses = iter(["Un flag d'activation", "Le pays de résidence du client"])
    result = self_consistency_eval(
        "OWNER.DIM_CLI.STA_CLI",
        catalog,
        _empty_lineage(),
        profile,
        lambda *_: next(responses),
        similarity_threshold=0.5,
    )
    assert result.fragile is True


def test_self_consistency_eval_stable_when_descriptions_agree():
    catalog = _catalog_with_comment()
    profile = Profile(generated_at=datetime.now())

    result = self_consistency_eval(
        "OWNER.DIM_CLI.STA_CLI",
        catalog,
        _empty_lineage(),
        profile,
        lambda *_: "Statut du client",
        similarity_threshold=0.5,
    )
    assert result.fragile is False
