"""Cache incrémental de l'étape extract (`prise-de-recul.md` §7.2).

Aucun appel LLM ici : on vérifie l'identité des fragments, la relecture du
cache et le réassemblage — c'est-à-dire tout ce qui rend un run reprenable.
"""

import json

from docmaker.models import Chunk, ChunkSet
from docmaker.pipeline.extract import _assemble, _chunk_key, _load_cache


def _chunk(doc="a.docx", heading="Ch. 1", text="La table ACCOUNT porte le solde."):
    return Chunk(doc=doc, heading_path=heading, text=text, start=0, end=len(text))


def test_key_follows_content_not_position():
    same = _chunk_key(_chunk()) == _chunk_key(_chunk())
    changed = _chunk_key(_chunk()) == _chunk_key(_chunk(text="Autre texte."))
    assert same and not changed


def test_truncated_cache_line_is_ignored(tmp_path):
    """Un kill en pleine écriture ne doit pas rendre le cache inutilisable."""
    path = tmp_path / "facts.jsonl"
    good = json.dumps({"key": "abc", "facts": {"tables": [], "relations": [], "notes": []}})
    path.write_text(good + '\n{"key": "def", "facts": {"tab', encoding="utf-8")
    assert list(_load_cache(path)) == ["abc"]


def test_missing_cache_is_not_an_error(tmp_path):
    assert _load_cache(tmp_path / "absent.jsonl") == {}


def test_assemble_attaches_provenance_and_skips_unextracted():
    extracted, missing = _chunk(), _chunk(heading="Ch. 2", text="Fragment jamais extrait.")
    cached = {
        _chunk_key(extracted): {
            "tables": [{"name": "ACCOUNT", "columns": [{"name": "BALANCE"}]}],
            "relations": [],
            "notes": [],
        }
    }
    facts = _assemble(ChunkSet(chunks=[extracted, missing]), cached)
    assert len(facts.tables) == 1
    # La provenance est rattachée par le code, jamais par le modèle.
    assert facts.tables[0].source_refs[0].file == "a.docx"
    assert facts.tables[0].source_refs[0].locator == "Ch. 1"


def test_stale_cache_entries_are_ignored():
    """Un fragment disparu du chunkset ne doit pas ressortir dans les faits."""
    cached = {"cle-orpheline": {"tables": [{"name": "OBSOLETE"}], "relations": [], "notes": []}}
    assert _assemble(ChunkSet(chunks=[_chunk()]), cached).tables == []
