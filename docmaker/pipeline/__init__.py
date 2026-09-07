"""Orchestration : suite d'étapes, chacune lit/écrit un artefact dans build/."""

from __future__ import annotations

import logging

from ..config import Settings
from . import chunk, extract, ingest, reconcile, render

log = logging.getLogger(__name__)

STAGES = {
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
