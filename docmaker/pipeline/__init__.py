"""Orchestration : suite d'étapes, chacune lit/écrit un artefact dans build/."""

import logging

from ..config import Settings
from . import chunk, extract, ingest, recon, reconcile, render

log = logging.getLogger(__name__)

STAGES = {
    # Reconnaissance : lecture seule sur le datamart, hors chaîne documentaire.
    # Se lance seule : stages = ["recon"] dans config.toml.
    "recon": recon.run,
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
