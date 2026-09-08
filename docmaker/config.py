"""Configuration du pipeline.

Ce fichier ne contient QUE le schéma (types + structure) : aucune valeur par
défaut. Toutes les valeurs viennent de `config.toml`. Seul le secret
`LLM_API_KEY` vient de l'environnement (ou d'un `.env`).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict, TomlConfigSettingsSource


class _Table(BaseModel):
    # Table TOML stricte : clé absente OU en trop => erreur au démarrage.
    model_config = ConfigDict(extra="forbid")


class LLMConfig(_Table):
    base_url: str
    model: str
    max_context_tokens: int
    max_retries: int
    temperature: float
    timeout: float

    @property
    def is_local(self) -> bool:
        host = urlparse(self.base_url).hostname or ""
        return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local")


class VLMConfig(_Table):
    enabled: bool
    model: str
    prompt: str


class ChunkConfig(_Table):
    max_chars: int
    overlap_chars: int
    min_chars: int


class Settings(BaseSettings):
    # extra="ignore" ici (pas "forbid") : sinon une variable non modélisée dans
    # .env ferait planter le chargement. Les champs requis ci-dessous suffisent à
    # garantir que config.toml est complet (une clé manquante => ValidationError).
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        toml_file="config.toml",
        extra="ignore",
    )

    source_dir: Path
    build_dir: Path
    out_dir: Path
    lang: str
    system_name: str
    formats: list[str]
    stages: list[str]

    llm: LLMConfig
    vlm: VLMConfig
    chunk: ChunkConfig

    # Hors TOML : fourni par l'environnement ou .env.
    llm_api_key: str = ""

    @classmethod
    def settings_customise_sources(cls, settings_cls, env_settings, dotenv_settings, **_):
        # priorité : env > .env > config.toml
        return env_settings, dotenv_settings, TomlConfigSettingsSource(settings_cls)
