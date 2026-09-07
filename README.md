# docmaker

Génère une documentation structurée et *RAG-ready* d'un système de base de données
à partir de sources hétérogènes (`.docx`, `.xlsx`, `.pptx`, `.pdf`).

Pipeline : `docling` → fragments → extraction de faits par LLM → réconciliation
(+ conflits) → Markdown à frontmatter + `chunks.jsonl`.

## Installation

```sh
mise use python@3.12      # ou pyenv/asdf — l'écosystème docling n'est pas fiable sur 3.14
poetry install
cp .env.example .env      # renseigner LLM_API_KEY (OpenRouter en test)
```

## Utilisation

Tout se configure dans `config.toml` — **aucune option en ligne de commande**.

```sh
cp mes_docs/* samples/
poetry run docmaker
```

- Artefacts intermédiaires : `build/`
- Documentation finale : `out/` (`index.md`, `tables/*.md`, `chunks.jsonl`)

Pour rejouer une seule étape, ajuster `stages` dans `config.toml`
(ex. `stages = ["extract", "reconcile", "render"]` pour itérer sans re-convertir).

## Étapes

| Étape       | Entrée                 | Sortie                              |
|-------------|------------------------|------------------------------------|
| `ingest`    | `samples/`             | `build/md/*.md`, `build/manifest.json` |
| `chunk`     | `build/md/`            | `build/chunks.json`                |
| `extract`   | `build/chunks.json`    | `build/facts.json`                 |
| `reconcile` | `build/facts.json`     | `build/model.json`                 |
| `render`    | `build/model.json`     | `out/*.md`, `out/chunks.jsonl`     |

## Tests

```sh
poetry run pytest
```

## Docs

- `docs/architecture-review.md` — revue d'archi, risques, benchmark, glossaire
- `docs/decisions.md` — choix d'implémentation de la v1 et limites assumées
