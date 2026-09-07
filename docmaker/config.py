"""Configuration unique du pipeline : config.toml + .env (secret)."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class LLMConfig(BaseModel):
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "anthropic/claude-3.5-haiku"
    max_context_tokens: int = 32_000
    max_retries: int = 3
    temperature: float = 0.0


class VLMConfig(BaseModel):
    enabled: bool = True
    model: str = "anthropic/claude-3.5-haiku"
    prompt: str = (
        "Décris ce diagramme de base de données : entités/tables, attributs lisibles, "
        "relations (cardinalités, clés étrangères). N'invente rien ; si illisible, indique-le."
    )


class ChunkConfig(BaseModel):
    max_chars: int = 12_000
    overlap_chars: int = 400
    min_chars: int = 200


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        toml_file="config.toml",
        extra="ignore",
    )

    llm_api_key: str = ""

    source_dir: Path = Path("samples")
    build_dir: Path = Path("build")
    out_dir: Path = Path("out")
    lang: str = "fr"
    system_name: str = "système"
    formats: list[str] = ["docx", "xlsx", "pptx", "pdf"]
    stages: list[str] = ["ingest", "chunk", "extract", "reconcile", "render"]

    llm: LLMConfig = LLMConfig()
    vlm: VLMConfig = VLMConfig()
    chunk: ChunkConfig = ChunkConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # priorité : env > .env > config.toml > défauts
        return (env_settings, dotenv_settings, TomlConfigSettingsSource(settings_cls))
