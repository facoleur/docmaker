"""Point d'entrée unique : `poetry run docmaker` (piloté par config.toml, pas d'arguments)."""

from __future__ import annotations

import logging

from rich.logging import RichHandler

from .config import Settings
from .pipeline import run


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    settings = Settings()
    log = logging.getLogger("docmaker")
    log.info(
        "source=%s | stages=%s | llm=%s | vlm=%s",
        settings.source_dir,
        ",".join(settings.stages),
        settings.llm.model,
        settings.vlm.model if settings.vlm.enabled else "off",
    )
    if not settings.llm_api_key and not settings.llm.is_local:
        log.warning("LLM_API_KEY vide — les étapes extract/render/VLM vont échouer.")
    run(settings)


if __name__ == "__main__":
    main()
