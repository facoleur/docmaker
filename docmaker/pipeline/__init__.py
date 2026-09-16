"""Orchestration : suite d'étapes, chacune lit/écrit un artefact dans build/."""

import logging

from ..config import Settings
from ..semantic import infer
from . import (
    catalog,
    chunk,
    extract,
    ingest,
    joins,
    lineage,
    priority,
    profile,
    recon,
    reconcile,
    render,
)

log = logging.getLogger(__name__)

STAGES = {
    # Couche sémantique Oracle (docs/poc-qualite-service.md) : lecture seule sur
    # le datamart, hors chaîne documentaire. Chaque étape lit l'artefact
    # produit par la précédente dans build/ — se lancent donc dans l'ordre
    # recon > catalog > priority > lineage > profile > joins > infer,
    # ex. stages = ["catalog"] pour rejouer une seule étape.
    "recon": recon.run,
    "catalog": catalog.run,
    "priority": priority.run,
    "lineage": lineage.run,
    "profile": profile.run,
    "joins": joins.run,
    "infer": infer.run,
    # Pipeline documentaire d'origine (docs Office → Markdown RAG-ready).
    "ingest": ingest.run,
    "chunk": chunk.run,
    "extract": extract.run,
    "reconcile": reconcile.run,
    "render": render.run,
}


def run(settings: Settings) -> None:
    settings.build_dir.mkdir(parents=True, exist_ok=True)
    for name in settings.stages:
        if name not in STAGES:
            raise ValueError(f"étape inconnue : {name!r} (connues : {', '.join(STAGES)})")
        log.info("── étape : %s ──", name)
        STAGES[name](settings)
    log.info("terminé")
