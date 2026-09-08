# docmaker

Génère une documentation structurée et _RAG-ready_ d'un système de base de données
à partir de sources hétérogènes (`.docx`, `.xlsx`, `.pptx`, `.pdf`).

Pipeline : `docling` → fragments → extraction par LLM (faits structurés +
notes libres) → réconciliation (+ conflits) → Markdown à frontmatter + `chunks.jsonl`.

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

| Étape       | Entrée              | Sortie                                 |
| ----------- | ------------------- | -------------------------------------- |
| `ingest`    | `samples/`          | `build/md/*.md`, `build/manifest.json` |
| `chunk`     | `build/md/`         | `build/chunks.json`                    |
| `extract`   | `build/chunks.json` | `build/facts.json`                     |
| `reconcile` | `build/facts.json`  | `build/model.json`                     |
| `render`    | `build/model.json`  | `out/*.md`, `out/chunks.jsonl`         |

## Architecture

Trois vues : le flux de données bout-en-bout, le chargement de la config, et la
boucle interne de `extract`.

### 1. Flux de données du pipeline

```mermaid
flowchart TD
    subgraph inputs["Entrées — hors pipeline"]
        SRC["samples/<br/>docx · xlsx · pptx · pdf"]
        TPL["docmaker/templates/<br/>table_reference.md.j2 · index.md.j2"]
    end

    subgraph llm["Services LLM — endpoint OpenAI-compatible"]
        LLMC["LLM.json() / LLM.text()<br/>modèle ≤ 30B"]
        VLMC["LLM.describe_image()<br/>VLM (si vlm.enabled)"]
    end

    ORCH["pipeline.run(settings)<br/>mkdir build/ · boucle settings.stages"]

    subgraph pipe["pipeline/ — STAGES, dans l'ordre"]
        ING["ingest.run"]
        CHK["chunk.run"]
        EXT["extract.run"]
        REC["reconcile.run<br/>+ conflicts.field_conflict"]
        RND["render.run"]
    end

    ORCH --> ING --> CHK --> EXT --> REC --> RND

    subgraph build["build/ — atelier reconstructible, un artefact par étape"]
        MD["md/*.md"]
        AST["assets/*  (images)"]
        MAN["manifest.json<br/>Manifest = list[SourceDoc]"]
        CJS["chunks.json<br/>ChunkSet = list[Chunk]"]
        FJS["facts.json<br/>Facts = TableFacts + RelationFacts + NoteFacts<br/>mentions NON dédupliquées + provenance"]
        MJS["model.json<br/>DocModel = Entity + relations + notes + conflicts"]
    end

    SRC --> ING
    ING -- "docling → export_to_markdown" --> MD
    ING -- "images extraites" --> AST
    ING -. "pixels → description" .-> VLMC
    ING --> MAN

    MAN --> CHK
    MD --> CHK
    CHK -- "découpe structurelle sur les titres<br/>+ hard-split si > max_chars" --> CJS

    CJS --> EXT
    EXT -- "1 appel / fragment · schéma FactSet (tables · relations · notes)" --> LLMC
    EXT -- "code rattache SourceRef(file, heading_path)" --> FJS

    FJS --> REC
    REC -- "fusion par nom normalisé · conflits type/key/nullable<br/>notes dédupliquées (texte exact)" --> MJS

    MJS --> RND
    TPL --> RND
    RND -. "prose d'intro uniquement, contrainte au JSON de l'entité" .-> LLMC

    subgraph out["out/ — livrable RAG"]
        OUT1["tables/{slug}.md"]
        OUT2["index.md"]
        OUT3["chunks.jsonl<br/>1 ligne/table + index : id, title, doc_type,<br/>entities, sources, text"]
    end

    RND -- "notes rattachées par nom de table" --> OUT1
    RND -- "notes transverses / non rattachées" --> OUT2
    RND --> OUT3
```

### 2. Chargement de la configuration

```mermaid
flowchart LR
    ENVV["variables d'env"] --> SET
    DOTENV[".env<br/>LLM_API_KEY"] --> SET
    TOML["config.toml<br/>(seule source des valeurs)"] --> SET

    SET["load_settings() → Settings<br/>pydantic-settings<br/>priorité : env > .env > toml<br/>clé manquante ⇒ ValidationError"]

    SET --> D["source_dir · build_dir · out_dir<br/>lang · system_name · formats · stages"]
    SET --> LC["llm : LLMConfig<br/>(is_local dérivé du base_url)"]
    SET --> VC["vlm : VLMConfig"]
    SET --> CC["chunk : ChunkConfig<br/>max_chars · overlap_chars"]

    SET --> MAIN["__main__.main()<br/>loge source/stages/llm/vlm<br/>warn si clé absente et non-local"]
    MAIN --> RUN["pipeline.run(settings)"]
```

### 3. Boucle interne de `extract` (JSON best-effort + retries)

```mermaid
sequenceDiagram
    participant EX as extract.run
    participant L as LLM.json
    participant API as endpoint LLM

    EX->>EX: charge build/chunks.json (ChunkSet)
    loop pour chaque Chunk
        EX->>L: json(prompt fragment, FactSet, system)
        loop essais 1..max_retries
            L->>API: chat.completions.create (temperature=0)
            API-->>L: texte brut
            L->>L: _strip (retire le bloc code, isole le 1er objet) + model_validate_json
            alt JSON conforme au schéma
                L-->>EX: FactSet
            else ValidationError / ValueError
                L->>API: "Invalide : err. Renvoie UNIQUEMENT le JSON corrigé"
            end
        end
        Note over L,EX: échec après N essais ⇒ RuntimeError<br/>extract logue l'erreur et passe au fragment suivant
        EX->>EX: TableFacts(**t, source_refs=[ref]) / RelationFacts(...) / NoteFacts(...)
    end
    EX->>EX: écrit build/facts.json (Facts)
```

### Points clés

- **Une seule direction :** `samples/` (jamais écrit) → `build/` (jetable,
  reconstructible) → `out/` (expédié, écrit par `render` seul).
- **Provenance ajoutée par le code, pas par le LLM :** le modèle ne renvoie qu'un
  `FactSet` plat ; `extract` et `reconcile` attachent/propagent les `SourceRef`.
- **Dédup tardive :** `facts.json` contient N mentions par table ; `reconcile`
  fusionne en une `Entity` et déverse les désaccords dans `conflicts`.
- **Faits + notes :** `extract` produit le modèle structuré (`tables`,
  `relations`) _et_ des `notes` en texte libre pour le contenu hors-schéma
  (règle métier, cycle de vie, rétention, glossaire…), rattachées à une table
  par nom ou classées transverses. Mêmes rails, même provenance ; elles
  atterrissent dans les fiches et dans l'index. Un doc 100 % prose n'est plus
  perdu, et le rattachement se fait au (re)run suivant dès que la table existe.
- **LLM cantonné :** extraction structurée + notes (`extract`) + 2–4 phrases
  d'intro (`render`) + description d'images (VLM). Les tableaux de colonnes sont
  rendus déterministiquement par Jinja.
- **`chunks.json` (build, fragments source pour le LLM) ≠ `chunks.jsonl` (out,
  unités RAG finales)** — noms proches, rôles opposés.

## Tests

```sh
poetry run pytest
```

## Docs

- `docs/architecture-review.md` — revue d'archi, risques, benchmark, glossaire
- `docs/decisions.md` — choix d'implémentation de la v1 et limites assumées
