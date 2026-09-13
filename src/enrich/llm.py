"""Client LLM unifie pour src/enrich/describe.py.

Bascule Ollama <-> OpenRouter via LLM_PROVIDER, sans dependance de package
supplementaire pour OpenRouter (HTTP brut, son API est deja compatible
OpenAI) : `ollama` reste la seule dependance ajoutee pour ce module.

  - "ollama" (defaut) : modele local, AUCUNE donnee ne sort du reseau de la
    banque. Le seul mode a utiliser sur de vraies donnees bancaires.
  - "openrouter" : reutilise LLM_API_KEY (deja utilise pour les tests du reste
    de docmaker) - envoie le contexte a un tiers externe. Tests/dev sur
    donnees non sensibles UNIQUEMENT, jamais en prod.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request

import ollama

logger = logging.getLogger(__name__)

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "proposed_value": {"type": "string"},
        "confidence": {"type": "number"},
        "confidence_reason": {"type": "string"},
    },
    "required": ["proposed_value", "confidence"],
}


class LLMClient:
    """Genere une sortie JSON contrainte a RESPONSE_SCHEMA, via Ollama ou OpenRouter."""

    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        provider: str | None = None,
        ollama_host: str | None = None,
        ollama_model: str | None = None,
        openrouter_model: str | None = None,
    ):
        self.provider = provider or os.environ.get("LLM_PROVIDER", "ollama")
        if self.provider not in ("ollama", "openrouter"):
            raise ValueError(
                f"LLM_PROVIDER inconnu: {self.provider!r} (attendu: ollama|openrouter)"
            )
        self.ollama_host = ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.ollama_model = ollama_model or os.environ.get("OLLAMA_MODEL", "qwen3")
        self.openrouter_model = openrouter_model or os.environ.get("OPENROUTER_MODEL")
        if self.provider == "openrouter" and not self.openrouter_model:
            raise ValueError(
                "OPENROUTER_MODEL n'est pas defini (requis quand LLM_PROVIDER=openrouter) - "
                "voir .env.example, ex: anthropic/claude-haiku-4.5"
            )

    @property
    def model_name(self) -> str:
        return self.ollama_model if self.provider == "ollama" else self.openrouter_model

    def generate(self, prompt: str) -> dict:
        """Retourne le JSON parse conforme a RESPONSE_SCHEMA. Logue chaque appel (avant/apres,
        avec duree) pour pouvoir distinguer un run lent d'un run bloque.
        """
        logger.info("appel LLM (%s/%s)...", self.provider, self.model_name)
        start = time.monotonic()
        try:
            result = (
                self._generate_ollama(prompt)
                if self.provider == "ollama"
                else self._generate_openrouter(prompt)
            )
        except Exception:
            logger.exception(
                "echec appel LLM (%s/%s) apres %.1fs",
                self.provider,
                self.model_name,
                time.monotonic() - start,
            )
            raise
        logger.info(
            "reponse LLM recue (%s/%s) en %.1fs",
            self.provider,
            self.model_name,
            time.monotonic() - start,
        )
        return result

    def _generate_ollama(self, prompt: str) -> dict:
        resp = ollama.Client(host=self.ollama_host).generate(
            model=self.ollama_model, prompt=prompt, format=RESPONSE_SCHEMA, stream=False
        )
        return json.loads(resp.response)

    def _generate_openrouter(self, prompt: str) -> dict:
        payload = json.dumps(
            {
                "model": self.openrouter_model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self.OPENROUTER_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.environ['LLM_API_KEY']}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
        content = body["choices"][0]["message"]["content"].strip()
        # response_format=json_object n'est pas garanti par tous les providers routes par
        # OpenRouter : certains renvoient quand meme un bloc markdown ```json ... ```.
        content = _CODE_FENCE.sub("", content).strip()
        return json.loads(content)
