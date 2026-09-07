# docmaker — état du projet & suite

Document d'onboarding. Dernière mise à jour : 2026-09-07.
À lire en premier, puis `decisions.md` (choix détaillés) et `architecture-review.md`
(archi cible, risques, benchmark, glossaire).

---

## But

Transformer des sources hétérogènes et peu structurées (docx, Excel de specs,
pptx, PDF, diagrammes, images) qui documentent un **système de base de données
bancaire** en une **documentation structurée, tracée et RAG-ready** en Markdown,
qui reprend au maximum l'existant pour limiter les hallucinations.

## État au 2026-09-07

- **v1 scaffoldée, jamais exécutée.** Le code compile, la logique pure (`chunk`,
  `llm._strip`) est testée en isolation. `poetry install` et un premier run réel
  restent à faire (nécessite Python 3.12, non installé sur la machine).
- Pas encore de jeu de sources de test dans `samples/`.
- Pas de dépôt git initialisé.

## Contraintes fermes (non négociables sauf nouvelle info)

| Contrainte | Détail |
|---|---|
| Modèle | ≤ 30B params, **on-prem** en prod |
| Function calling | supposé **non fiable** → extraction par JSON parsé + retries |
| Endpoint de test | OpenRouter, `LLM_API_KEY` dans `.env`, base URL `https://openrouter.ai/api/v1` |
| Modèle de test | `anthropic/claude-3.5-haiku` (texte + vision), à remplacer par l'endpoint 30B |
| Contexte | 256k annoncé → on plafonne à 32k |
| Exécution | séquentielle (pas de parallélisme LLM pour l'instant) |
| Interface | **pas de CLI** — tout dans `config.toml`, un seul point d'entrée |
| Python | cible 3.12 (docling/torch pas fiables sur 3.14) |
| Objectif de sortie | corpus RAG (pas Obsidian, pas de site généré) |

## Architecture (résumé)

Pipeline de 5 étapes, chacune lit/écrit **un artefact JSON dans `build/`**
→ rejouable et inspectable étape par étape (`stages` dans `config.toml`).

```
samples/*.{docx,xlsx,pptx,pdf}
  │  ingest   (docling ; VLM décrit les images PDF)
  ▼
build/md/*.md  +  build/manifest.json   (inventaire, sha256, assets non traités)
  │  chunk    (découpe sur les titres markdown + hard-split)
  ▼
build/chunks.json
  │  extract  (1 appel LLM / fragment → FactSet ; provenance rattachée par le code)
  ▼
build/facts.json
  │  reconcile (merge par nom normalisé ; conflits via module pur)
  ▼
build/model.json
  │  render   (Jinja pour les tableaux ; LLM seulement pour l'intro, groundée)
  ▼
out/index.md  +  out/tables/*.md  +  out/chunks.jsonl   (RAG-ready)
```

Principe anti-hallucination central : **les faits structurés (colonnes, types,
contraintes) sont *rendus* depuis `model.json`, jamais générés.** Le LLM n'écrit
que de la prose d'introduction, contrainte au JSON de l'entité concernée.

## Ce qui est implémenté

- Conversion multi-format via docling, avec manifeste (statut, sha256, comptage
  des images non exploitées).
- Description VLM des images **quand docling fournit les pixels** (PDF surtout).
- Chunking structurel sans tokenizer.
- Extraction LLM → schéma `FactSet` plat (tables / colonnes / relations),
  validé Pydantic, avec boucle de correction JSON (3 essais).
- Réconciliation par nom normalisé (tables + colonnes) + `build/model.json`.
- Détection de conflits dans `pipeline/conflicts.py` (module **pur**, sans I/O
  ni LLM) sur `type`, `nullable`, `key` des colonnes.
- Rendu Jinja : `tables/<slug>.md` avec frontmatter riche + `index.md` +
  `chunks.jsonl` (1 enregistrement par doc : id, title, doc_type, entities,
  sources, text).
- Frontmatter : `id, title, doc_type, system, domain, entities, sources,
  generated_by, generated_at, review_status, confidence, conflicts`
  (`confidence: low` auto si conflits ; `review_status: needs_review` partout).
- Tests `pytest` sans réseau : `chunk`, `conflicts`, `reconcile`, `llm`.

## Caveats / limites assumées de la v1

1. **Perte à l'extraction non éliminée, seulement tracée.** Images hors PDF,
   objets dessin, diagrammes embarqués Excel/Word : si docling ne rend pas les
   pixels, ils finissent en `unhandled_assets` dans `manifest.json`. À vérifier à
   la main après chaque run.
2. **Excel traité par docling tel quel.** Feuilles à cellules fusionnées /
   multi-tables / diagrammes : extraction probablement médiocre. Pas encore de
   parseur dédié.
3. **Pas de retrieval sémantique** à la rédaction : les faits sont passés par
   entité, sans embeddings.
4. **Conflits limités aux colonnes** (type/nullable/key). Cardinalités de
   relations, descriptions divergentes : non couverts.
5. **Provenance grossière** : `fichier` + chemin de titre. Les offsets caractères
   sont calculés mais pas exploités en aval (pas d'ancrage précis dans la source).
6. **Pas de reprise incrémentale** : relancer une étape réécrit tout son artefact ;
   pas de cache par hash de fragment.
7. **`chunks.jsonl` = markdown rendu par page.** Le rechunking fin pour embeddings
   est laissé à l'étape RAG en aval.
8. **Qualité non mesurée.** Aucun outillage de benchmark dans cette v1 ; la
   validation repose entièrement sur relecture humaine (`review_status`).
9. **Modèle VLM de test ≠ contrainte 30B.** `claude-3.5-haiku` en test ; en prod
   il faudra un VLM ≤ 30B (piste : `Qwen2.5-VL-32B`).
10. **docling est lourd** (tire torch & co). Premier `poetry install` long.
11. **Petit modèle = extraction bruitée attendue.** Le schéma `FactSet` est
    volontairement plat pour maximiser la fiabilité, mais la précision/rappel
    réels sont inconnus tant qu'on n'a pas de gold set.

## Risques majeurs (détail dans `architecture-review.md` §3)

- Perte silencieuse à l'extraction (n°1) — mitigation : inventaire + spot-check.
- Hallucination plausible de détails de schéma — mitigation : rendu déterministe
  des tableaux, intro groundée, `review_status`.
- Sources contradictoires — mitigation : détection de conflits (partielle en v1).
- Terminologie incohérente entre sections — **non mitigé en v1** (pas de glossaire
  contrôlé construit en amont).
- Données bancaires sensibles envoyées au modèle — mitigation : on-prem en prod,
  flags `pii`/`regulatory` prévus dans le frontmatter mais pas encore alimentés.

## Suite

### Court terme (débloquer / valider)

- [ ] Installer Python 3.12 (`mise use python@3.12`), `poetry install`.
- [ ] Rassembler un petit jeu de sources représentatif dans `samples/`.
- [ ] Premier run `stages = ["ingest"]` seul → relire `manifest.json` et les
      `.md` : mesurer *ce qui est perdu* (images, feuilles Excel, diagrammes).
- [ ] Run complet sur ce petit jeu, relire `out/` avec un œil métier.
- [ ] `git init` + premier commit.

### Moyen terme (qualité)

- [ ] **Gold set** : 2-3 bundles + doc « correcte » validée par un expert sur
      ~10 tables / 5 règles. Base de tout le reste.
- [ ] Métriques : recall/précision au niveau *fait*, groundedness, validité des
      citations (cf. `architecture-review.md` §4).
- [ ] LLM-as-judge (gros modèle) calibré sur le gold set pour la fidélité.
- [ ] Glossaire contrôlé construit **avant** la rédaction, réinjecté dans chaque
      prompt (cohérence terminologique).
- [ ] Étendre les types de faits : règles métier, flux/intégrations, glossaire.
- [ ] Parseur Excel dédié (bypass docling) si la qualité est insuffisante.
- [ ] Alimenter `pii`/`regulatory` dans le frontmatter.

### Plus tard (passage à l'échelle)

- [ ] Parallélisation de `extract` (quand l'endpoint le permet).
- [ ] Reprise incrémentale (cache par hash de fragment / de source).
- [ ] VLM de prod ≤ 30B et évaluation dédiée des descriptions de diagrammes.
- [ ] Détection de conflits élargie (cardinalités, descriptions).
- [ ] Rechunking fin + pipeline d'embeddings pour le RAG aval.

## Démarrage rapide

```sh
cd ~/projects/docmaker
mise use python@3.12
poetry install
cp .env.example .env        # coller la clé OpenRouter
cp ~/tes_docs/* samples/
poetry run docmaker         # piloté par config.toml
poetry run pytest
```
