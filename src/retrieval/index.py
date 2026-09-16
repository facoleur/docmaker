"""Index semantique : embeddings locaux + recherche par produit scalaire.

Artefact persiste dans `build/retrieval/`, dans le meme esprit que les artefacts
JSON par etape du pipeline : un index est un fichier versionnable et rejouable,
pas un service a exploiter. A l'echelle du datamart (~10^3 documents) la recherche
exacte en numpy est instantanee ; un moteur type Elasticsearch ne se justifierait
qu'a partir de ~10^5 documents ou pour servir plusieurs utilisateurs concurrents.

Le modele d'embedding tourne en local (ONNX/CPU via fastembed) : aucune metadonnee
ne sort de la machine, seul le telechargement initial des poids est un flux entrant.
Les poids sont caches dans MODEL_CACHE_DIR (surchargeable via DOCMAKER_MODEL_CACHE) :
le defaut de fastembed est /tmp, qui est un tmpfs sur cette machine et rendrait donc
un retelechargement obligatoire a chaque redemarrage.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from src.retrieval.document import Document

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# Poids du canal "sens" dans le melange des deux canaux. Cale sur le golden set du
# notebook : recall@3 de 2/5 (identite seule) a 4/5. A reajuster quand le catalogue
# sera majoritairement documente - ici 5 tables sur 101 le sont, regime tres asymetrique.
MEANING_WEIGHT = 0.5
INDEX_DIR = Path(__file__).resolve().parents[2] / "build" / "retrieval"
MODEL_CACHE_DIR = Path(
    os.environ.get("DOCMAKER_MODEL_CACHE", Path.home() / ".cache" / "docmaker" / "embeddings")
)


def _embedder(model_name: str):
    """Import tardif : catalog.py et document.py restent utilisables (et testables)
    sans fastembed installe.
    """
    from fastembed import TextEmbedding

    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(model_name, cache_dir=str(MODEL_CACHE_DIR))


def _normalize(matrix: np.ndarray) -> np.ndarray:
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def corpus_hash(documents: list[Document]) -> str:
    """Empreinte du corpus : permet de detecter qu'un index sur disque est perime
    (une description ajoutee dans OMD change le hash) sans avoir a reembedder.
    """
    digest = hashlib.sha256()
    for document in documents:
        digest.update(document.fqn.encode("utf-8"))
        digest.update(document.identity_text.encode("utf-8"))
        digest.update(document.meaning_text.encode("utf-8"))
    return digest.hexdigest()[:16]


def _embed_meanings(model, documents: list[Document], dimensions: int) -> np.ndarray:
    """Embeddings des textes de sens, zero pour les entites non documentees (on ne
    paie l'embedding que pour celles qui ont quelque chose a dire).
    """
    matrix = np.zeros((len(documents), dimensions))
    positions = [i for i, d in enumerate(documents) if d.has_meaning]
    if positions:
        vectors = _normalize(
            np.array(list(model.embed([documents[i].meaning_text for i in positions])))
        )
        matrix[positions] = vectors
    return matrix


@dataclass
class SearchHit:
    score: float
    document: Document


@dataclass
class SemanticIndex:
    """Deux matrices : les identifiants deplies et, quand elle existe, la
    documentation.

    Score d'une entite documentee : `max(identite, melange des deux canaux)`. Les
    deux autres fusions testees sont moins bonnes sur le golden set du notebook :
    le melange seul (3/5) peut faire *baisser* une entite dont la description matche
    moins bien que son nom - documenter ne devrait jamais nuire ; le max des deux
    canaux bruts (3/5) fait dominer toute entite documentee quelle que soit la
    question, parce qu'une phrase francaise matche mieux une question francaise que
    des identifiants deplies. Le max du melange (4/5) est monotone sans cette
    inflation.
    """

    documents: list[Document]
    embeddings: np.ndarray  # identite
    meaning_embeddings: np.ndarray  # sens (lignes nulles si non documente)
    model_name: str = MODEL_NAME

    @classmethod
    def build(cls, documents: list[Document], model_name: str = MODEL_NAME) -> SemanticIndex:
        model = _embedder(model_name)
        embeddings = _normalize(np.array(list(model.embed([d.identity_text for d in documents]))))
        meanings = _embed_meanings(model, documents, embeddings.shape[1])
        logger.info(
            "index construit : %s, dont %d document(s) documente(s)",
            embeddings.shape,
            sum(1 for d in documents if d.has_meaning),
        )
        return cls(
            documents=documents,
            embeddings=embeddings,
            meaning_embeddings=meanings,
            model_name=model_name,
        )

    def save(self, directory: Path = INDEX_DIR) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "embeddings.npy", self.embeddings)
        np.save(directory / "meaning_embeddings.npy", self.meaning_embeddings)
        with (directory / "documents.jsonl").open("w", encoding="utf-8") as handle:
            for document in self.documents:
                handle.write(json.dumps(document.to_dict(), ensure_ascii=False) + "\n")
        (directory / "meta.json").write_text(
            json.dumps(
                {
                    "model": self.model_name,
                    "documents": len(self.documents),
                    "documented": sum(1 for d in self.documents if d.has_meaning),
                    "dimensions": int(self.embeddings.shape[1]),
                    "corpus_hash": corpus_hash(self.documents),
                    "built_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return directory

    @classmethod
    def load(cls, directory: Path = INDEX_DIR) -> SemanticIndex:
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        documents = [
            Document.from_dict(json.loads(line))
            for line in (directory / "documents.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return cls(
            documents=documents,
            embeddings=np.load(directory / "embeddings.npy"),
            meaning_embeddings=np.load(directory / "meaning_embeddings.npy"),
            model_name=meta["model"],
        )

    def is_stale(self, documents: list[Document]) -> bool:
        """True si le catalogue a bouge depuis la construction de l'index."""
        return corpus_hash(documents) != corpus_hash(self.documents)

    def search(
        self,
        question: str,
        top_k: int = 5,
        entity_type: str | None = None,
        schema: str | None = None,
        tags: set[str] | None = None,
        described_only: bool = False,
    ) -> list[SearchHit]:
        """Recherche filtree. Les filtres s'appliquent en masque sur les scores :
        a cette echelle il n'y a aucun interet a pre-filtrer le corpus, et le masque
        garde la semantique simple ("les meilleurs parmi ceux qui passent le filtre").
        """
        model = _embedder(self.model_name)
        vector = next(iter(model.embed([question])))
        vector = vector / np.linalg.norm(vector)
        documented = np.array([d.has_meaning for d in self.documents])
        identity_scores = self.embeddings @ vector
        meaning_scores = self.meaning_embeddings @ vector
        blended = (1 - MEANING_WEIGHT) * identity_scores + MEANING_WEIGHT * meaning_scores
        scores = np.where(documented, np.maximum(identity_scores, blended), identity_scores)

        keep = np.ones(len(self.documents), dtype=bool)
        for position, document in enumerate(self.documents):
            if entity_type and document.entity_type != entity_type:
                keep[position] = False
            elif schema and document.schema.lower() != schema.lower():
                keep[position] = False
            elif tags and not tags.intersection(document.tags):
                keep[position] = False
            elif described_only and not document.has_description:
                keep[position] = False
        scores = np.where(keep, scores, -np.inf)

        best = np.argsort(-scores)[:top_k]
        return [
            SearchHit(score=float(scores[i]), document=self.documents[i])
            for i in best
            if np.isfinite(scores[i])
        ]
