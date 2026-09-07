# Revue d'architecture — pipeline de documentation SGBD

Contexte : générer une documentation structurée d'un système de base de données
(domaine bancaire) à partir de sources peu structurées (docx, Excel de specs avec
diagrammes en bordel, images). Contrainte : modèle ≤ 30B, on-prem.

---

## 1. Flow proposé initialement

1. Documents → Markdown via [docling](#docling).
2. Ingestion fragment par fragment → résumé du fragment + des précédents
   ([refine-chain / résumé roulant](#refine-chain-résumé-roulant)) jusqu'à un
   résumé complet.
3. Structuration d'une doc avec [ToC](#toc).
4. Rédaction d'une doc Markdown structurée, reprenant l'existant au maximum pour
   limiter les [hallucinations](#hallucination), avec [frontmatter](#frontmatter).

**Verdict** : squelette sain (extraction → agrégation → structuration → rédaction
[groundée](#grounding-groundedness)), mais l'étape 2 est le point faible.

### Pourquoi le résumé roulant est un anti-pattern ici

- **Perte compounding** : au fragment 50, un détail comme
  `account_balance DECIMAL(18,2) NOT NULL` a été « résumé » et a disparu. Or
  c'est ce que la doc de référence doit garantir.
- **Dépendance à l'ordre** : sources en bordel = pas d'ordre naturel ; le
  résultat du refine-chain dépend de l'ordre de passage.
- **Dérive du petit modèle** : sur une longue chaîne de raffinement, un 30B perd
  le fil et accumule les erreurs.
- Un résumé sert à un humain qui survole, pas à reconstruire une spec technique
  précise. Mauvais artefact intermédiaire.

---

## 2. Architecture recommandée : extraction de faits, pas de prose

Remplacer le résumé roulant par un [map-reduce](#map-reduce) vers un **store de
faits typés**.

### Étape A — Extraction (map, parallélisable, sans dérive)

Chaque fragment traité **indépendamment**. Le modèle extrait des faits atomiques
avec [provenance](#provenance) :

- entités : `{table, colonne, type, contraintes, description, source_ref}`
- relations : `{table_a, table_b, cardinalité, FK, source_ref}`
- règles métier, termes de glossaire, flux / intégrations

Stockés en JSON ou SQLite.

### Étape B — Réconciliation (reduce)

- Dédup et merge par entité (une table citée dans 5 docs → 1 entrée).
- **Les conflits ne sont pas résolus silencieusement** : ils sont remontés
  (« specs.xlsx dit `NOT NULL`, model.docx dit nullable »). Précieux pour les
  [SME](#sme).

### Étape C — Structuration

Ne pas demander au modèle d'inventer un plan. Définir soi-même le **squelette
type** d'une doc de SGBD ; la liste d'entités du store génère mécaniquement les
sections « par table ».

Sections types : Overview · Glossaire · Modèle logique · Schéma physique (par
table) · Relations / [ERD](#erd) · Règles métier · Flux / intégrations ·
Sécurité / accès · Questions ouvertes.

### Étape D — Rédaction

- Par section, en [RAG](#rag) sur le store + les [spans](#span) sources.
- Le modèle **n'écrit que ce qui est dans le matériel fourni** ; chaque
  affirmation cite un `source_ref`.
- Post-traitement : vérifier que chaque citation résout.

### Corollaire fort

Les tables de colonnes / types / contraintes ne doivent pas être *générées* mais
**rendues déterministiquement** (template [Jinja](#jinja)) depuis le store. Le
modèle écrit la prose (overview, descriptions, règles) ; jamais les données
structurées. Réduit massivement la surface d'hallucination.

### Bénéfices

Traçabilité totale · détection de conflits · pas de perte compounding · le store
**est** la garantie [anti-hallucination](#grounding-groundedness).

---

## 3. Risques

| # | Risque | Mitigation |
|---|--------|-----------|
| 1 | **Perte silencieuse à l'extraction** — diagrammes embarqués Excel/Word, objets dessin, images : docling les droppe souvent sans bruit. **Risque n°1.** | Inventaire exhaustif (chaque fichier, feuille, image) + log extrait vs ignoré + métrique de couverture + spot-check humain. Pour Excel : bypasser docling (openpyxl / pandas + logique par type de feuille). |
| 2 | **Diagrammes = sémantique perdue** : l'[OCR](#ocr) donne des tokens déconnectés, pas des entités / cardinalités. | Passe [VLM](#vlm) dédiée (ex. Qwen2.5-VL-7B/32B) avec prompt « décris ce diagramme DB : entités, relations, cardinalités, clés ». Puis vérif humaine. |
| 3 | **Hallucination plausible** : le petit modèle comble les trous avec assurance (invente un type, une FK). | Grounding strict, vérification des citations, registre « inconnu / à confirmer » explicite. |
| 4 | **Conflits sources résolus silencieusement**. | Détection au merge (étape B). |
| 5 | **Terminologie incohérente** entre sections (même concept, noms différents). | Construire le glossaire contrôlé *en premier*, le réinjecter dans chaque prompt de rédaction. |
| 6 | **Limites de [contexte](#fenêtre-de-contexte)** du 30B : gros schémas, longues tables ne rentrent pas. | Chunking respectant les frontières de table ([HybridChunker](#hybridchunker) de docling), pas du fixed-size. |
| 7 | **Domaine bancaire** : [PII](#pii) / données confidentielles envoyées au modèle. | On-prem obligatoire, à acter. Flags `pii` / `regulatory` sur les sections. |
| 8 | **Pas de vérité terrain** : impossible de valider la sortie sans SME. | [Gold set](#gold-set) (§4). |
| 9 | **Staleness** : les sources changent, la doc générée dérive. | Hash des inputs par section, détection de péremption. |
| 10 | **Sur-confiance** : du Markdown propre avec frontmatter *lit* comme faisant autorité même quand `confidence: low`. | Bandeau visible sur les sections non validées. |

---

## 4. Benchmark

### Gold set

2-3 bundles de sources représentatifs ; un SME valide la doc « correcte » sur un
sous-ensemble (ex. 10 tables, 5 règles métier). Seul ancrage fiable.

### Métriques (par ordre d'importance)

1. **Recall / précision au niveau *fait*** (le cœur) : des faits qu'un SME peut
   énumérer depuis les sources, combien le pipeline en capture
   ([recall](#recall-rappel)) ? Des faits capturés, combien sont corrects et
   réellement présents dans la source ([précision](#précision)) ?
2. **[Groundedness](#grounding-groundedness) / fidélité** : échantillonner N
   phrases de sortie, vérifier que chacune est soutenue par sa citation. % soutenu
   / % contredit / % non soutenu.
3. **Validité des citations** : les `source_ref` résolvent-ils et contiennent-ils
   vraiment l'affirmation ? Entièrement automatisable.
4. **Couverture** : fraction du contenu source représentée dans la sortie ;
   fraction de la sortie adossée à une citation.
5. **Taux de détection de conflits** : injecter des conflits connus, vérifier
   qu'ils remontent.
6. **Cohérence** : même entité décrite pareil entre sections ; usage des termes
   du glossaire.
7. **Taux d'hallucination** : affirmations non traçables à aucune source. Cible
   ~0 pour les faits de schéma.

### Process

- **[LLM-as-judge](#llm-as-judge)** avec un gros modèle (Claude ou équivalent)
  pour fidélité / couverture sur large échantillon — même si la prod tourne en
  30B. Calibrer le juge contre les labels humains du gold set.
- **Suite de régression** : inputs figés, hash des sorties, diff à chaque
  changement de pipeline.
- **[Ablations](#ablation)** : refine-chain vs map-reduce ; avec / sans VLM sur
  diagrammes ; 32B vs 7B ; tailles de chunk.
- **Éval humaine** : le SME note chaque section 1-5 (exactitude, complétude,
  utilité) et mesure surtout le **temps de mise en production** (temps pour
  rendre la section utilisable) — la vraie métrique business.
- **Proxy pas cher en dev** : test round-trip. Donner au pipeline un doc dont on
  connaît le schéma, mesurer le recall des faits connus.

---

## 5. Frontmatter proposé

```yaml
---
id: account-reference
title: "Table ACCOUNT — référence"
doc_type: table_reference   # overview | table_reference | glossary | data_flow | business_rules | erd
system: <nom du système>
domain: banking
entities: [account]          # tables/objets couverts
sources:
  - file: specs_v3.xlsx
    sheet: "Comptes"
    ranges: ["A1:F80"]
    sha256: 3f2a...
  - file: architecture.docx
    pages: [4, 5]
    sha256: 9b11...
generated_by: qwen2.5-32b
generated_at: 2026-09-07
inputs_checksum: a17c...      # hash des faits d'entrée -> detecte la peremption
source_coverage: 0.82        # fraction de la section adossee a une citation
confidence: medium           # high | medium | low
review_status: needs_review  # draft | needs_review | validated
reviewed_by: null
open_questions:
  - "Cardinalité ACCOUNT<->CUSTOMER non spécifiée dans les sources"
conflicts:
  - "specs.xlsx: BALANCE NOT NULL / model.docx: nullable"
flags: [pii, regulatory]
related: [customer-reference, account-lifecycle]
---
```

Champs qui portent la valeur : `sources`, `review_status`, `confidence`,
`open_questions`, `conflicts`, `inputs_checksum`. Le reste est du confort.

---

## 6. Mini-glossaire

### docling
Bibliothèque open source qui convertit des documents (PDF, docx, xlsx, images…)
en Markdown / JSON structuré. Gère la mise en page, les tables, l'OCR. Point
faible : les diagrammes et objets dessin embarqués, souvent perdus.

### refine-chain (résumé roulant)
Stratégie de résumé itérative : on résume le fragment 1, puis on donne
« résumé courant + fragment 2 » au modèle pour produire un nouveau résumé, etc.
Simple mais **lossy** : l'information ancienne est progressivement écrasée.

### map-reduce
Patron de traitement en deux temps : *map* = appliquer un traitement à chaque
élément indépendamment (parallélisable) ; *reduce* = fusionner tous les
résultats. Ici : extraire les faits de chaque fragment (map), puis les
réconcilier (reduce). Pas de dérive, pas de dépendance à l'ordre.

### ToC
*Table of Contents* — table des matières / plan du document.

### hallucination
Sortie d'un LLM qui **paraît plausible mais est fausse ou inventée** (un type de
colonne, une clé étrangère qui n'existe nulle part dans les sources).

### grounding / groundedness
« Ancrage ». Le fait qu'une affirmation générée soit **effectivement soutenue par
une source fournie**. Une sortie *groundée* ne dit rien qui ne soit traçable au
matériel d'entrée. La *groundedness* est la métrique qui mesure ce taux.

### frontmatter
Bloc de métadonnées en tête d'un fichier Markdown, délimité par `---`, au format
YAML. Sert à porter titre, provenance, statut de revue, tags, etc.

### provenance
Traçabilité d'une information : de quel fichier / feuille / page / plage de
cellules elle provient. Stockée sous forme de `source_ref` sur chaque fait.

### span
Portion continue d'un document source (un intervalle de caractères, une plage de
cellules, une zone de page) citée comme preuve d'une affirmation.

### RAG
*Retrieval-Augmented Generation*. On récupère (*retrieval*) les passages
pertinents dans une base, on les injecte dans le prompt, et le modèle rédige en
s'appuyant **uniquement** sur ces passages. Réduit les hallucinations.

### ERD
*Entity-Relationship Diagram* — diagramme entités-associations : représente les
tables, leurs attributs, et les relations (cardinalités, clés étrangères).

### Jinja
Moteur de templates Python. Permet de générer du texte / Markdown
déterministiquement à partir de données (ex. rendre un tableau de colonnes depuis
le store de faits, sans passer par le LLM).

### OCR
*Optical Character Recognition* — reconnaissance de caractères : extrait le texte
d'une image. Donne des chaînes de caractères, **pas** la structure sémantique
d'un diagramme.

### VLM
*Vision-Language Model* — modèle multimodal capable de « lire » une image et d'en
produire une description textuelle. Nécessaire pour transformer un diagramme en
description exploitable (entités, relations, cardinalités).

### fenêtre de contexte
Quantité maximale de texte (mesurée en *tokens*) qu'un modèle peut traiter en une
fois : prompt + sortie. Un 30B a typiquement 32k–128k tokens, avec une qualité de
raisonnement qui se dégrade bien avant la limite.

### HybridChunker
Découpeur de docling qui segmente un document en respectant sa structure
(sections, tables, titres) plutôt qu'en coupant tous les N caractères.

### PII
*Personally Identifiable Information* — données à caractère personnel (nom,
IBAN, numéro client…). En bancaire, leur traitement est réglementé : d'où
l'exigence on-prem et les flags `pii` / `regulatory`.

### SME
*Subject-Matter Expert* — expert métier. Ici : la personne qui connaît réellement
le schéma et peut valider la doc générée.

### gold set
Jeu de référence : un échantillon de sources pour lequel un humain a produit (ou
validé) la sortie « correcte ». Sert d'étalon pour mesurer la qualité du
pipeline.

### recall (rappel)
Parmi tout ce qui *aurait dû* être trouvé, la proportion effectivement trouvée.
Recall faible = on **oublie** des faits.

### précision
Parmi tout ce qui a été trouvé / affirmé, la proportion **correcte**. Précision
faible = on **invente** ou on se trompe.

### LLM-as-judge
Utiliser un (gros) LLM pour noter automatiquement les sorties d'un autre modèle
(fidélité, couverture…). À calibrer contre des jugements humains pour être
fiable.

### ablation
Test consistant à retirer / changer **un seul** composant du pipeline pour
mesurer sa contribution réelle (ex. « avec vs sans VLM », « 32B vs 7B »).
