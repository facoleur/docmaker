from docmaker.config import ChunkConfig
from docmaker.pipeline.chunk import _sections, _split

MD = (
    "# Titre\n\n"
    + "intro " + "x" * 300 + "\n\n"
    + "## Section A\n\n"
    + "a" * 400 + "\n\n"
    + "## Section B\n\n"
    + "b" * 50 + "\n"
)


def test_sections_build_heading_paths():
    paths = [p for p, _, _ in _sections(MD)]
    assert "Titre" in paths
    assert "Titre / Section A" in paths
    assert "Titre / Section B" in paths


def test_going_back_up_truncates_deeper_levels():
    md = "# A\n\ncontent-a\n\n## B\n\ncontent-b\n\n# C\n\ncontent-c\n"
    paths = [p for p, _, _ in _sections(md)]
    assert "A / B" in paths
    assert "C" in paths  # pas "A / B / C"


def test_split_keeps_small_sections_and_respects_max():
    cfg = ChunkConfig(max_chars=200, overlap_chars=40)
    chunks = [
        c
        for path, body, start in _sections(MD)
        for c in _split("d.md", path, body, start, cfg)
    ]
    assert chunks
    assert all(len(c.text) <= cfg.max_chars for c in chunks)
    assert all(c.heading_path for c in chunks)
    # "Section B" (50 chars) est petite mais conservée : plus de filtre min_chars
    assert any(c.heading_path.endswith("Section B") for c in chunks)
