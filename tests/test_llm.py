import pytest

from docmaker.llm import LLM, _strip
from docmaker.models import FactSet


def test_strip_removes_code_fence():
    assert _strip('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_isolates_object_from_surrounding_prose():
    assert _strip('Voici le JSON : {"a": 1} — fin') == '{"a": 1}'


class _FakeLLM(LLM):
    """LLM sans réseau : _chat renvoie des réponses préchargées."""

    def __init__(self, replies):
        self._replies = list(replies)
        self._c = type("C", (), {"max_retries": 3, "model": "m", "temperature": 0.0})()
        self._vlm = type("V", (), {"model": "m"})()

    def _chat(self, messages, model=None):
        return self._replies.pop(0)


def test_json_retries_then_parses():
    llm = _FakeLLM(["pas du json", '{"tables": [{"name": "T", "columns": []}], "relations": []}'])
    result = llm.json("x", FactSet)
    assert result.tables[0].name == "T"


def test_json_gives_up_after_max_retries():
    llm = _FakeLLM(["nope", "toujours nope", "encore raté"])
    with pytest.raises(RuntimeError):
        llm.json("x", FactSet)
