"""Client LLM minimal : endpoint OpenAI-compatible, sortie JSON best-effort (retries).

On ne suppose PAS de function calling fiable : on demande du JSON brut, on le nettoie
et on le valide contre un schéma Pydantic, avec quelques tentatives de correction.
"""

from __future__ import annotations

import base64
import json
import logging
import re

from openai import OpenAI
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

_JSON_INSTRUCTION = (
    "Réponds avec UN SEUL objet JSON valide, sans texte autour ni bloc de code. "
    "Il doit être conforme à ce JSON Schema :\n"
)
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\n?|\n?```$")


class LLM:
    def __init__(self, settings) -> None:
        self._c = settings.llm
        self._vlm = settings.vlm
        self._client = OpenAI(base_url=settings.llm.base_url, api_key=settings.llm_api_key)

    def json(self, prompt: str, schema: type[BaseModel], *, system: str = "") -> BaseModel:
        sys = (system + "\n" if system else "") + _JSON_INSTRUCTION + json.dumps(
            schema.model_json_schema(), ensure_ascii=False
        )
        messages: list[dict] = [
            {"role": "system", "content": sys},
            {"role": "user", "content": prompt},
        ]
        err = ""
        for attempt in range(1, self._c.max_retries + 1):
            raw = self._chat(messages)
            try:
                return schema.model_validate_json(_strip(raw))
            except (ValidationError, ValueError) as exc:
                err = str(exc)[:300]
                log.warning("JSON invalide (essai %d/%d) : %s", attempt, self._c.max_retries, err)
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"Invalide : {err}. Renvoie UNIQUEMENT le JSON corrigé."},
                ]
        raise RuntimeError(f"pas de JSON valide après {self._c.max_retries} essais : {err}")

    def text(self, prompt: str, *, system: str = "") -> str:
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": prompt})
        return self._chat(messages).strip()

    def describe_image(self, png: bytes, prompt: str) -> str:
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        return self._chat(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }
            ],
            model=self._vlm.model,
        ).strip()

    def _chat(self, messages: list[dict], model: str | None = None) -> str:
        resp = self._client.chat.completions.create(
            model=model or self._c.model,
            messages=messages,
            temperature=self._c.temperature,
        )
        return resp.choices[0].message.content or ""


def _strip(raw: str) -> str:
    """Retire un éventuel bloc ```…``` et isole le premier objet {...}."""
    s = raw.strip()
    if s.startswith("```"):
        s = _FENCE_RE.sub("", s).strip()
    i, j = s.find("{"), s.rfind("}")
    return s[i : j + 1] if 0 <= i < j else s
