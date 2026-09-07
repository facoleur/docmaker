# Décisions d'implémentation — v1

Date : 2026-09-07. Portée : première implémentation minimale.

## Contexte figé (réponses reçues)

- Modèle cible **≤ 30B**, on-prem, **pas de function calling / tooling fiable**
  → extraction par JSON parsé + tentatives de correction.
- Test via **OpenRouter** ; `LLM_API_KEY` dans `.env`.
  Oui, il faut une base URL : `https://openrouter.ai/api/v1` (dans `config.toml`).
- Contexte annoncé 256k → plafonné à **32k** (conservateur).
- Traitement **séquentiel** (pas de parallélisme LLM).
- **VLM activé** pour les diagrammes ; modèle non tranché → placeholder
  `anthropic/claude-3.5-haiku` en test, `Qwen2.5-VL-32B` recommandé en prod.
- Formats v1 : `docx`, `xlsx`, `pptx`, `pdf` via docling.
- **Pas de CLI** : tout dans `config.toml`, un seul point d'entrée.
- Objectif : produire un corpus **RAG-ready** (pas Obsidian, pas de site).

## Décisions

| Sujet | Choix | Raison |
|---|---|---|
| Python | cible 3.12 (`^3.12`) | écosystème docling/torch pas sûr sur 3.14 |
| Packaging | Poetry (format `[tool.poetry]` legacy, OK en Poetry 2.4) | imposé |
| Point d'entrée | `poetry run docmaker` → `docmaker/__main__.py`, piloté par `config.toml` | pas de CLI demandé |
| Config | `pydantic-settings` : `config.toml` + `.env` (secret) | une seule source déclarative, typée |
| Orchestration | 5 étapes (`ingest`, `chunk`, `extract`, `reconcile`, `render`) ; chacune lit/écrit un artefact JSON dans `build/` | rejouable étape par étape, inspectable, benchmarkable |
| Conversion | `docling` (tous formats) | demandé ; seul outil multi-format correct |
| Chunking | découpe sur les titres Markdown + hard-split avec overlap si trop long | zéro dépendance, pas de tokenizer ; HybridChunker docling écarté en v1 (deps, complexité) |
| Extraction | 1 appel LLM par fragment → schéma `FactSet` (tables/colonnes/relations) volontairement plat, validé Pydantic ; provenance rattachée par le code | schéma minimal = plus fiable sur petit modèle ; grounding par construction |
| Store de faits | `build/facts.json` puis `build/model.json` (JSON, pas SQLite) | diffable, éditable à la main entre `reconcile` et `render` (checkpoint humain) |
| Réconciliation | fusion par nom normalisé (tables + colonnes) | dédoublonnage inter-documents |
| Conflits | module `pipeline/conflicts.py` **pur** (sans LLM ni I/O) ; v1 : champs `type`, `nullable`, `key` des colonnes | découplé comme demandé ; périmètre restreint (les descriptions divergent trop pour être utiles) |
| Rendu | Jinja2 : le **tableau des colonnes est rendu déterministiquement** depuis le modèle ; seule la prose d'intro passe par le LLM, contrainte au JSON de l'entité | surface d'hallucination réduite au minimum |
| Sortie RAG | `out/tables/*.md` + `out/index.md` (frontmatter) + `out/chunks.jsonl` (un enregistrement par doc : `id`, `title`, `doc_type`, `entities`, `sources`, `text`) | prêt à ingérer dans un vector store |
| Frontmatter | `id, title, doc_type, system, domain, entities, sources, generated_by, generated_at, review_status, confidence, conflicts` | provenance + statut de revue = l'essentiel ; `confidence: low` automatique si conflits |
| Client LLM | SDK `openai` pointé sur l'endpoint ; helpers `json()` (retries + nettoyage de fences + extraction `{...}`), `text()`, `describe_image()` | pas de dépendance `instructor` : le function calling est supposé non fiable de toute façon |
| Logs | `logging` stdlib + `rich` | lisibilité d'un pipeline lent |
| Lint | `ruff` | standard, rapide (mypy : à ajouter plus tard) |
| Tests | `pytest` : `chunk`, `conflicts`, `reconcile`, `llm` (parsing/retry), sans réseau | cœur logique couvert, LLM mocké |

## Limites connues de la v1 (assumées)

- Images hors PDF : la récupération des pixels dépend de docling ; sinon comptées
  dans `unhandled_assets` du `manifest.json` (perte tracée, jamais silencieuse).
- Pas de retrieval sémantique à la rédaction : les faits sont passés par entité.
- Conflits limités aux colonnes (type / nullable / key).
- Provenance = `fichier` + chemin de titre ; les offsets caractères sont calculés
  mais pas exploités en aval.
- Pas de reprise incrémentale : relancer une étape réécrit tout son artefact.
- `chunks.jsonl` contient le Markdown rendu par page ; le rechunking fin pour
  embeddings est laissé à l'étape RAG en aval.

## Ouvert / à trancher plus tard

- Modèle VLM de prod (`Qwen2.5-VL-32B` ?).
- Extraction Excel dédiée (bypass docling) si la qualité docling est insuffisante.
- Types de faits non couverts en v1 : glossaire, règles métier, flux/intégrations.
- Outillage de benchmark (cf. `architecture-review.md` §4) : hors périmètre v1.
- Parallélisation de l'étape `extract` quand l'endpoint le permettra.
