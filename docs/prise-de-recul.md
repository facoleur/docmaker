# docmaker — prise de recul : ce que c'est, ce que ça pourrait être, ce qu'il faut savoir

Date : 2026-09-09. Document de réflexion, pas de spécification.
Complément critique à `project-status.md` (état), `decisions.md` (choix v1) et
`architecture-review.md` (archi cible). Il n'annule rien : il questionne les
prémisses, propose des architectures franchement différentes, et liste les
pièges connus de cette famille de projets.

---

## 1. Ce que j'ai compris

### Le problème posé

Un système de base de données bancaire est documenté — mal — dans un tas de
fichiers hétérogènes : `.docx` de conception, `.xlsx` de specs avec des
diagrammes collés dedans, `.pptx` de présentation, `.pdf` d'architecture, des
images de MCD. Cette information est **vraie mais illisible** : dispersée,
redondante, contradictoire, non requêtable. Personne ne peut répondre à
« quelles sont les colonnes de `ACCOUNT` et laquelle porte le solde » sans
ouvrir cinq fichiers.

`docmaker` transforme ce tas en **un corpus Markdown structuré, tracé et
RAG-ready** : une fiche par table, un index, un `chunks.jsonl` prêt à indexer.

### Le vrai « pourquoi »

Le livrable n'est pas la doc. Le livrable, c'est **la capacité à interroger un
système que plus personne ne maîtrise entièrement**, sans mobiliser l'expert qui
le connaît. La doc Markdown n'est qu'un format intermédiaire lisible par un
humain _et_ par un retriever. C'est un projet de **récupération de connaissance
tacite**, avec la contrainte que la connaissance récupérée doit être **fiable ou
explicitement marquée comme douteuse** — parce qu'en bancaire, une doc fausse et
confiante coûte plus cher que pas de doc du tout.

### La thèse architecturale actuelle

Le pipeline est bâti sur une intuition juste, formulée dans
`architecture-review.md` §2 : **on n'utilise pas un LLM pour écrire une doc, on
l'utilise pour extraire des faits, et on rend la doc mécaniquement**.

Concrètement, quatre paris s'enchaînent :

| Pari | Formulation                                                                      | Solidité                                                                     |
| ---- | -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| P1   | Un ≤30B sait _extraire_ (map) mais pas _synthétiser sans dériver_ (refine-chain) | **Très solide.** Le rejet du résumé roulant est le meilleur choix du projet. |
| P2   | Le contenu utile est majoritairement un schéma de tables                         | **Fragile.** Déjà entamé par le commit « non-table information ».            |
| P3   | Deux mentions de la même table peuvent être fusionnées par nom normalisé         | **Fragile.** Voir §5.3 et §5.4.                                              |
| P4   | Rendre les tableaux depuis un store ⇒ pas d'hallucination de schéma              | **Solide sur la forme, incomplet sur le fond.** Voir §7.1.                   |

L'axe anti-hallucination — provenance attachée par le code, jamais par le
modèle ; tableaux rendus par Jinja ; `review_status: needs_review` partout ;
conflits remontés au lieu d'être résolus — est structurellement correct. C'est
la partie du projet qu'il ne faut pas défaire.

### L'état réel

**Le pipeline n'a jamais tourné sur un seul document réel.** `samples/` est
vide, `build/` et `out/` n'existent pas. Tout ce qui est écrit ci-dessus décrit
une architecture _pensée_, pas une architecture _validée_. C'est le fait le plus
important du projet aujourd'hui, et il conditionne tout le reste de ce document.

---

## 2. La question à poser avant tout le reste

**Est-ce qu'on a accès à la base elle-même ?**

Si oui — même en lecture seule, même sur un environnement de recette, même sous
forme d'un export DDL ou d'un dump de `INFORMATION_SCHEMA` — alors une requête
SQL de quinze lignes donne **gratuitement, exhaustivement et sans une seule
hallucination** ce que tout le pipeline `ingest → chunk → extract → reconcile`
essaie de reconstruire par inférence : la liste des tables, des colonnes, des
types, des `NOT NULL`, des clés primaires et étrangères, des index, des vues.

Dans ce cas l'architecture ne se corrige pas, **elle s'inverse** :

```
catalogue SQL / DDL  ──►  squelette factuel exhaustif (vérité, 0 hallucination)
                              ▲
documents .docx/.xlsx  ──►  enrichissement sémantique attaché au squelette
   (extraction LLM)          (à quoi ça sert, règles métier, historique, pièges)
```

Le LLM ne construit plus le modèle : il **habille un modèle déjà vrai**. Son
espace d'erreur passe de « inventer une table » à « attribuer une phrase à la
mauvaise colonne » — et cette erreur-là est détectable, parce que la cible est
un ensemble fermé et connu.

Le gain est d'un ordre de grandeur, pas marginal. Cette question mérite d'être
posée avant d'écrire une ligne de plus. En bancaire, l'accès est souvent refusé
— mais « refusé » est une réponse qu'il faut avoir obtenue, pas supposée. Et les
substituts sont nombreux : script de création livré par l'éditeur, export
d'un outil de modélisation (PowerDesigner, Erwin), copybooks COBOL, mapping
d'un ETL, schéma d'un réplica analytique, catalogue d'un datawarehouse.

Le reste de ce document suppose que la réponse est non.

---

## 3. Six architectures franchement différentes

Pas des variantes de réglage : des façons différentes de découper le problème.
Chacune est jugée sur ce qu'elle coûte et ce qu'elle règle.

### 3.1 — Vocabulaire fermé : le LLM classe au lieu de générer

**Aujourd'hui :** chaque fragment est envoyé au modèle qui _invente_ la liste
des tables qu'il y voit. `reconcile` recolle ensuite les morceaux par
normalisation de chaîne, en espérant que `ACCOUNT`, `Account`, `T_ACCOUNT` et
`la table des comptes` retombent sur leurs pattes. Elles ne retombent pas.

**À la place :** deux passes.

1. **Passe lexique, déterministe.** Un balayage sans LLM de tous les Markdown
   produit la liste des identifiants candidats : tokens en `UPPER_SNAKE_CASE`,
   en-têtes de colonnes de tableaux, noms de feuilles Excel, mots suivis de
   `TABLE`/`table`. On agrège par fréquence, on dédoublonne, et **un humain
   valide la liste en dix minutes**. On obtient un vocabulaire fermé de N tables.
2. **Passe extraction, contrainte.** Le prompt devient : _« Voici la liste des
   tables connues du système : […]. Dans ce fragment, laquelle ou lesquelles
   sont décrites, et qu'en dit-il ? Si aucune, réponds vide. »_

Le modèle passe de **générateur** (tâche ouverte, dure, hallucinogène) à
**classifieur** (tâche fermée, facile, vérifiable). C'est la transformation qui
paie le plus sur un ≤30B, et de loin.

Effets de bord : la réconciliation par normalisation de chaîne disparaît (les
clés sont canoniques dès l'extraction) ; une table absente du lexique devient
un signal explicite (« fragment non rattachable ») au lieu d'une entrée fantôme.

**Coût :** une passe de lexique (~150 lignes, sans LLM) + un point de validation
humaine. **Verdict : à faire, quelle que soit l'architecture retenue.**

### 3.2 — Ne pas fusionner du tout : la fiche multi-sources

**Aujourd'hui :** `reconcile` fabrique _une_ vérité par colonne. Quand deux
sources divergent sur le type, `_first()` prend la première valeur non vide —
c'est-à-dire, en pratique, celle du fichier dont le nom vient en premier dans
l'ordre alphabétique — et l'affiche dans le tableau comme un fait. Le conflit
est certes listé plus bas, mais le tableau, lui, ment.

**À la place :** on n'unifie jamais. Chaque source garde sa voix, et la fiche
n'affiche une valeur unique que là où les sources sont d'accord :

```markdown
| Colonne       | Type                            | Null    | Sources                 |
| ------------- | ------------------------------- | ------- | ----------------------- |
| `ACCOUNT_ID`  | `BIGINT`                        | non     | 3 sources concordantes  |
| `BALANCE`     | `DECIMAL(18,2)` **/** `NUMERIC` | non/oui | divergent (2024 / 2019) |
| `LEGACY_FLAG` | `CHAR(1)`                       | oui     | 2019 seule              |
```

Le détail de chaque désaccord est développé sous le tableau, avec la date et
l'autorité de chaque source. **Forme complète et modèle de données : annexe A.**

C'est **moins élégant et beaucoup plus honnête**. Ça reconnaît la nature réelle
du matériau : ces documents ne décrivent pas le même système, ils décrivent le
même système _à des dates différentes_. Fusionner un doc de 2019 et un de 2024
ne produit pas la vérité, ça produit une **chimère qui n'a jamais existé**.

Et pour l'usager réel — l'expert qui relit — savoir _qui dit quoi_ est
précisément l'information de valeur : c'est ce qui lui permet de trancher en
trois secondes. Le modèle unifié lui retire cette information pour lui rendre un
verdict qu'il ne peut pas auditer.

**Coût :** à peu près neutre en volume de code — `reconcile` calcule un statut
d'accord au lieu d'une valeur, `conflicts.py` perd sa raison d'être, le template
gagne trois régimes d'affichage. Le vrai coût est la datation des sources
(annexe A.3). **Verdict : sérieusement envisageable. Ma préférence si le corpus
est daté et hétérogène** — ce qui est l'hypothèse de départ du projet.

### 3.3 — Le baseline qu'on n'a pas mesuré : indexer sans générer

L'objectif final est un corpus RAG. Or **un corpus RAG n'a pas besoin d'être
rédigé**. Une architecture minimale :

```
sources ──► docling ──► markdown ──► chunks
                                       │
                                       ▼
                        LLM = étiqueteur, pas rédacteur
                        (« quelles tables ce fragment mentionne-t-il ?
                          quel type de contenu : schéma / règle / process ? »)
                                       │
                                       ▼
                        chunks.jsonl enrichi de métadonnées ──► vector store
```

Aucun texte n'est généré. Le taux d'hallucination est **structurellement nul** :
tout ce que le RAG restitue est un extrait littéral d'un document d'origine.
Le LLM ne produit que des étiquettes (ensemble fermé, vérifiables). C'est une à
deux journées de travail.

Le point important n'est pas que cette approche soit meilleure. **C'est qu'elle
est la baseline contre laquelle le pipeline complet doit se justifier.** Tant
qu'on n'a pas mesuré l'écart entre « RAG sur les sources brutes étiquetées » et
« RAG sur la doc générée », on ne sait pas ce que les cinq étapes apportent. Il
est parfaitement possible que la réponse soit « 80 % de la valeur pour 10 % du
code » — et il est tout aussi possible que la réponse soit « rien, parce que les
sources brutes sont illisibles hors contexte ». Les deux sont des informations
qui valent cher, et aucune n'est disponible aujourd'hui.

**Coût :** 1-2 jours. **Verdict : à construire en premier, avant toute autre
amélioration.** C'est l'instrument de mesure du projet.

### 3.4 — Le pipeline produit une file de travail, pas une documentation

**Aujourd'hui :** la machine produit une doc, l'humain la relit
(`review_status: needs_review` sur chaque page). Relire trois cents fiches
Markdown générées est une tâche que personne ne fait. Le `needs_review` va
rester `needs_review` pour toujours, et la doc sera consommée quand même.

**À la place :** l'artefact du pipeline n'est pas `out/`, c'est une **file de
faits candidats à statuer**, triés par confiance croissante, chacun présenté
avec son fragment source en regard :

```
[ 47/312 ]  ACCOUNT.BALANCE   type = DECIMAL(18,2)
            source : specs_v3.xlsx » Comptes » ligne 34
            ┌─ « BALANCE | DECIMAL(18,2) | Solde disponible du compte, NOT NULL »
            confiance : haute (littéral dans la source)
            [v] valider   [e] éditer   [x] rejeter   [?] à demander à Untel
```

Un TUI ou une page web statique. La doc devient le **sous-produit** des
décisions humaines, et chaque fait porte un `validated_by`. La métrique
« temps de mise en production » identifiée dans `architecture-review.md` §4
devient mesurable directement : elle _est_ le temps passé dans la file.

Le renversement conceptuel : le pipeline n'automatise pas la documentation, il
**automatise la préparation du travail de l'expert**. C'est ce qui fait
réellement adopter ce genre d'outil en entreprise. Une doc générée à 85 % juste
est inutilisable ; une file de 300 faits pré-remplis qu'un expert traite en une
demi-journée transforme un projet de six mois en un projet d'une semaine.

**Coût :** un front minimal (une page HTML + un JSON suffisent) et un état
persistant des validations. **Verdict : c'est ce qui décidera de l'adoption.
À garder en ligne de mire même si ce n'est pas la v1.**

### 3.5 — Adaptateurs par format plutôt que « tout en Markdown »

**Aujourd'hui :** docling aplatit `.xlsx`, `.docx`, `.pptx` et `.pdf` en une
seule représentation Markdown. C'est commode, et c'est précisément là que se
produit le **risque n°1 déjà identifié** (`architecture-review.md` §3.1).

Un `.xlsx` de specs bancaires n'est pas un document : c'est une grille où le
sens est porté par des choses que le Markdown ne sait pas représenter — la
position d'une zone, les cellules fusionnées d'un en-tête sur deux niveaux, une
couleur de fond qui signifie « déprécié », un commentaire de cellule qui porte
la règle métier, une formule qui _est_ la règle de calcul, un onglet masqué, un
objet dessin flottant au-dessus de la grille. Aplatir tout ça produit un texte
plausible et faux.

**À la place :** un adaptateur par format, chacun produisant une représentation
_fidèle à sa nature_ — `openpyxl` pour Excel (plages, styles, commentaires,
formules, feuille par feuille avec détection des zones de tableau),
`python-docx` pour Word (flux + tables + objets embarqués énumérés),
`python-pptx` pour PowerPoint (une slide = une unité, texte + notes + formes),
docling uniquement pour le PDF où il excelle.

Chaque adaptateur produit **aussi son propre inventaire** : n cellules, n
feuilles, n objets, n images, dont x traités. La couverture devient une mesure,
pas une supposition.

**Coût :** significatif (~1 semaine pour les trois adaptateurs). **Verdict :
ne pas décider avant d'avoir vu la sortie de docling sur les vrais fichiers.**
Si le premier run sur un `.xlsx` réel donne une bouillie — pronostic probable —
c'est le chantier prioritaire, parce qu'aucune quantité d'ingénierie de prompt
ne rattrape une information perdue avant le LLM.

### 3.6 — Triplets ouverts plutôt que schéma fermé

**Aujourd'hui :** `FactSet` impose trois formes — `tables`, `relations`,
`notes`. Le commit « take into account non-table information » a ajouté `notes`
précisément parce que les deux premières ne suffisaient pas. C'est le symptôme
d'un schéma trop étroit pour son matériau, et `notes` — un champ texte libre —
est la soupape qui va tout absorber.

**À la place :** un fait générique `(sujet, prédicat, objet, source_ref,
confiance)` avec un vocabulaire de prédicats contrôlé mais extensible
(`a_pour_colonne`, `a_pour_type`, `référence`, `règle_de_gestion`,
`durée_de_rétention`, `remplacé_par`, `alimenté_par`…). L'agrégation devient une
requête ; ajouter un type de fait ne demande plus de toucher au schéma Pydantic,
aux templates et à `reconcile`.

**Le compromis est réel :** un schéma serré contraint le petit modèle et le rend
fiable ; un schéma ouvert accepte tout et laisse entrer le bruit. La version
raisonnable est **hybride** : schéma serré pour ce qui est structurel (tables,
colonnes, relations — là où la précision est non négociable), triplets ouverts
pour le reste, avec une liste de prédicats fournie dans le prompt.

**Coût :** refonte de `models.py` et de `reconcile`. **Verdict : pertinent si
et seulement si le premier run montre que les documents parlent surtout d'autre
chose que de schéma.** À décider avec les données en main, pas avant.

---

## 4. Ce que je ferais, dans l'ordre

Une seule chose bloque tout : **le pipeline n'a jamais vu un document réel.**
Toutes les décisions ci-dessus dépendent de faits qu'on obtiendra en une
journée et qu'on ne peut obtenir autrement.

1. **Poser la question du §2** (accès à la base / au DDL / à un export de
   modélisation). Coût : un mail. Gain potentiel : la moitié du projet.
2. **Cinq documents réels dans `samples/`**, les plus représentatifs et les plus
   pénibles — surtout le `.xlsx` avec les diagrammes dedans.
3. **`stages = ["ingest"]` seul.** Lire les `.md` produits à côté des fichiers
   d'origine. Répondre à quatre questions : _que perd-on ? les titres Markdown
   existent-ils (le chunking en dépend entièrement) ? les tableaux survivent-ils ?
   les diagrammes arrivent-ils au VLM ?_
4. **Selon la réponse**, arbitrer §3.5 (adaptateurs) — c'est ici que ça se joue.
5. **Un gold set minuscule** : 20 faits sur 3 tables, dans un YAML écrit à la
   main en une heure. Pas 200. Vingt suffisent à détecter une régression, et
   c'est la différence entre régler des prompts au jugé et les régler pour de bon.
6. **Le baseline du §3.3** en parallèle, comme instrument de mesure.
7. **Puis** §3.1 (vocabulaire fermé) et §3.2 (fiche multi-sources) — les deux
   plus gros gains de fiabilité par unité de code écrit.

Ce que je ne ferais **pas** maintenant : parallélisation, reprise incrémentale,
LLM-as-judge, métriques élaborées, VLM de prod. Tout cela optimise un pipeline
dont on ignore encore s'il fonctionne.

---

## 5. Ce qu'il faut savoir sur ce genre de projet

Les régularités de la famille « extraction de connaissance depuis des documents
d'entreprise par LLM ». Elles se vérifient à peu près partout.

### 5.1 — La qualité de sortie est plafonnée par l'extraction, pas par le modèle

Ce qui n'est pas sorti du `.xlsx` n'existera jamais en aval, quel que soit le
modèle. On passe systématiquement trop de temps sur les prompts et pas assez sur
les parseurs. **Règle : investir dans l'ingestion jusqu'à ce que la perte soit
mesurée et acceptée, avant de toucher aux prompts.**

### 5.2 — Le goulot d'étranglement est humain, pas technique

Le pipeline produira des faits plus vite que l'expert ne peut les valider. Toute
architecture qui ne conçoit pas explicitement le poste de travail de l'expert
produit un artefact que personne ne valide, donc que personne n'ose utiliser,
donc que personne n'utilise. C'est le mode d'échec n°1 de ces projets — bien
avant l'hallucination.

### 5.3 — L'identité des entités est le problème difficile

`ACCOUNT`, `ACCOUNTS`, `T_ACCOUNT`, `TB_ACCOUNT`, `CPT`, `la table des comptes`,
`ACCOUNT_V2` : combien d'entités ? La normalisation de chaîne ne répondra jamais.
Toute solution robuste passe par un **vocabulaire fermé validé par un humain**
(§3.1) ou par un rapprochement explicite avec un catalogue réel (§2). C'est
l'endroit où les pipelines naïfs se cassent silencieusement — en produisant deux
fiches à moitié remplies au lieu d'une complète, sans que rien ne le signale.

### 5.4 — Les documents ont une date, et le pipeline doit le savoir

Un corpus d'entreprise décrit un système à **plusieurs états historiques
superposés**. Fusionner sans tenir compte du temps produit un modèle qui n'a
jamais existé. Un champ `date` par source — même approximatif, même déduit du
nom de fichier ou de la date de modification — et une règle de préséance
explicite valent mieux qu'un merge arbitraire. → **développé en annexe A.**

### 5.5 — Le format de sortie a plus d'influence qu'on ne croit

Du Markdown propre avec frontmatter **lit comme faisant autorité**, y compris
avec `confidence: low` dans un en-tête que personne ne regarde. Le degré
d'incertitude doit être **dans le corps du texte**, à l'endroit exact où il
s'applique : `BALANCE | ⚠ DECIMAL(18,2) ou NUMERIC selon la source | …` et non
relégué dans une section « divergences » en bas de page.

### 5.6 — Un modèle contraint bat un modèle intelligent

Sur un ≤30B, l'écart entre « génère la structure » et « choisis dans cette
liste » est plus grand que l'écart entre un 7B et un 70B. Chaque fois qu'une
tâche peut être reformulée en classification, en extraction littérale ou en
vérification binaire, la fiabilité fait un bond. C'est le principe directeur de
toute la §3.

### 5.7 — Le décodage contraint existe, et il rend le parsing best-effort obsolète

« Function calling non fiable » est une contrainte juste. Mais elle a été
traduite en « on parse du texte et on réessaie trois fois », alors que la
réponse technique adéquate est le **décodage contraint par grammaire**, qui
opère au niveau du sampler et rend le JSON invalide _structurellement
impossible_, indépendamment de la compétence du modèle :

- **vLLM** : `guided_json` / `response_format: {"type": "json_schema"}` (XGrammar)
- **llama.cpp** : grammaires GBNF
- **Ollama** : paramètre `format` avec un JSON Schema
- **TGI** : `grammar`

C'est une question à poser à l'équipe qui hébergera le modèle : **quel serveur
d'inférence ?** Si c'est vLLM ou Ollama — les deux cas les plus probables — la
boucle de retry de `llm.py` peut devenir une garantie. La retenir de toute façon
comme filet, mais elle ne devrait plus jamais se déclencher.

### 5.8 — Vérifier une extraction coûte dix fois moins cher que la produire

Deux vérifications, très rentables, absentes du pipeline :

- **Ancrage littéral (gratuit, sans LLM).** Un nom de table ou de colonne extrait
  qui n'apparaît pas _verbatim_ — à la casse et à la ponctuation près — dans le
  fragment source est presque toujours une invention. Trente lignes de code
  éliminent l'essentiel des hallucinations d'identifiants. À poser en §5.1 du
  pipeline : ce qui ne passe pas le filtre part dans un `rejected.json` qu'on
  inspecte, pas à la poubelle.
- **Passe de vérification (un appel LLM).** _« Voici un fragment et un fait
  extrait. Le fait est-il énoncé dans le fragment ? présent / partiel /
  absent. »_ Classification ternaire : tâche facile même pour un 7B, gain de
  précision considérable pour un coût marginal.

### 5.9 — Sans cache, l'itération est impossible

Chaque changement de prompt rejoue tous les appels. Sur des milliers de
fragments avec un 30B local et une exécution séquentielle, c'est plusieurs
heures et le projet cesse d'être itérable. Un cache disque indexé par
`sha256(fragment + prompt + modèle + schéma)` est une vingtaine de lignes et
change complètement le rythme de travail. À écrire avant le premier run
complet, pas après.

### 5.10 — Le cycle de mise à jour tue ces projets plus souvent que la qualité

Le jour où un expert corrige une fiche à la main, la regénération suivante
l'écrase. Il faut trancher **avant** le premier run partagé : soit les
corrections remontent dans les sources, soit elles vivent dans un fichier
d'overrides fusionné au `render`, soit la doc générée est explicitement
jetable. Ne pas trancher, c'est perdre le travail de l'expert une fois — et le
perdre une fois suffit à perdre l'expert.

### 5.11 — Bancaire : le garde-fou doit être dans le code

`samples/` + OpenRouter, c'est de la donnée bancaire chez un tiers américain.
Le README et les docs le disent, mais **une convention documentée n'a jamais
arrêté personne**. Une vérification au démarrage — un flag `sensitive = true`
dans `config.toml` qui fait échouer le run si `llm.is_local` est faux — coûte
cinq lignes et transforme un incident potentiel en message d'erreur. Le
prédicat `is_local` existe déjà dans `config.py`, il n'est simplement pas
utilisé pour ça.

---

## 6. Les erreurs à éviter

### Les trois grandes

1. **Construire le pipeline avant d'avoir vu les sources.** Cinq étapes, des
   templates, une détection de conflits — et `samples/` est vide. Chaque
   décision de design repose sur une hypothèse quant à la forme des documents.
   Une demi-journée passée à ouvrir dix fichiers réels invalidera probablement
   plusieurs de ces hypothèses, et il vaut mieux le savoir maintenant.

2. **Ne pas avoir de baseline.** Sans point de comparaison, « le pipeline
   marche » est une impression. La baseline du §3.3 se construit en un ou deux
   jours et rend toutes les décisions ultérieures mesurables au lieu
   d'argumentées.

3. **Optimiser la précision d'extraction au lieu du temps d'expert.** La métrique
   qui décide de la survie du projet est _combien de temps faut-il à l'expert
   pour rendre la doc utilisable_. Un pipeline à 95 % de précision dont personne
   ne peut auditer les 5 % restants est pire qu'un pipeline à 80 % dont chaque
   fait est traçable en un clic.

### Les pièges classiques

4. **Reporter le gold set.** Sans lui, chaque réglage de prompt est un
   changement à l'aveugle qui peut aussi bien dégrader. Vingt faits écrits à la
   main coûtent une heure et suppriment ce risque définitivement.
5. **Fusionner des sources de dates différentes** (§5.4).
6. **Traiter le silence comme une absence.** Un document dont le LLM ne tire
   rien : est-il vide de faits, ou l'extraction a-t-elle échoué ? Sans distinguer
   les deux, la perte redevient silencieuse — le risque n°1 rentre par la
   fenêtre après avoir été chassé par la porte.
7. **Croire un `review_status: needs_review` que personne ne lira.** Concevoir la
   revue comme un flux de travail (§3.4), pas comme un champ de métadonnée.
8. **Faire générer par le LLM ce qui peut être calculé.** L'intro de deux à
   quatre phrases par table, c'est un appel LLM par entité — trois cents appels
   pour de la prose que personne ne lit et qui rouvre une surface
   d'hallucination fermée partout ailleurs. À rendre optionnelle
   (`render.overview = false`), voire à supprimer.
9. **Optimiser avant de mesurer.** Parallélisation, cache incrémental,
   LLM-as-judge : tout cela optimise un pipeline dont on ne connaît pas encore
   la qualité de sortie. Le seul « prématuré » qui vaut le coup est le cache
   (§5.9), parce qu'il conditionne la vitesse d'itération, pas la performance.
10. **Livrer sans dire ce qui manque.** Un `index.md` doit énoncer sa propre
    couverture : _n documents traités, m en erreur, k images non exploitées,
    p fragments non rattachés_. Une doc qui ne dit pas ce qu'elle ignore est
    lue comme exhaustive.

---

## 7. Observations concrètes sur le code actuel

Pas une revue exhaustive : les points qui touchent aux garanties annoncées.

> **Statut au 2026-09-11.** §7.1, §7.2, §7.3 et §7.4 sont **corrigés** — le
> constat ci-dessous est conservé tel quel, il documente le pourquoi de chaque
> correction. §7.5 et §7.6 restent ouverts : ils ne se vérifient qu'au premier
> run sur un document réel. Détail dans `todo.md`, section « Dette connue ».

### 7.1 — Le tableau rendu « déterministiquement » contient un choix arbitraire

`reconcile.py`, `_first()` : en cas de divergence de type entre deux sources, la
valeur retenue est la première non vide rencontrée — donc dépendante de l'ordre
de parcours des fichiers. Elle est ensuite affichée dans le tableau Markdown
sans marque particulière. Le conflit est bien listé plus bas, mais la promesse
« les faits sont rendus, jamais générés » est entamée : **la cellule affiche un
arbitrage silencieux**. Le correctif est petit — passer le conflit au template
et rendre `⚠ DECIMAL(18,2) | NUMERIC` dans la cellule — et il restaure la
garantie centrale du projet. Le correctif de fond est en annexe A.

### 7.2 — Un run long peut tout perdre

`extract.py` n'intercepte que `RuntimeError`. Une `APIError`, un timeout réseau,
une coupure d'endpoint remontent et arrêtent le pipeline — or `facts.json` n'est
écrit qu'à la toute fin. Sur un run de plusieurs heures, une erreur au 90ᵉ
pourcent perd l'intégralité du travail. Écriture incrémentale (un JSONL en
append) ou cache par fragment (§5.9) : l'un ou l'autre suffit, et le cache règle
les deux problèmes à la fois.

### 7.3 — Faux conflits sur les types

`conflicts.field_conflict` regroupe par `casefold()` seul. `VARCHAR(20)`,
`varchar (20)` et `VARCHAR (20)` produisent trois groupes, donc un conflit, donc
`confidence: low` sur la fiche. Sur un corpus réel, ce bruit noiera les vraies
divergences — et un signal de conflit auquel on cesse de croire ne vaut rien.
Une normalisation des types (espaces, casse, synonymes `INT`/`INTEGER`,
`NUMERIC`/`DECIMAL`) est nécessaire avant comparaison.

### 7.4 — Le conflit `nullable` perd sa provenance

`reconcile.py` construit les valeurs de conflit `nullable` avec `source: ""`,
là où `type` et `key` passent par `field_conflict` qui la conserve. La fiche
affichera « `True` — source ? ». Incohérence à corriger.

### 7.5 — Le chunking repose entièrement sur des titres Markdown

`chunk._sections` découpe sur `#` … `######`. Si docling ne produit pas de
titres pour les `.xlsx` et les `.pptx` — hypothèse à vérifier au premier run —
tout le document devient un seul fragment racine, hard-splité tous les
12 000 caractères, **au milieu des lignes de tableau**. Le pire cas possible
pour l'extraction : des tableaux coupés en deux, sans en-tête sur la seconde
moitié. À vérifier à l'étape 3 du §4 ; si c'est le cas, un découpage
supplémentaire sur les frontières de tableau Markdown s'impose.

### 7.6 — Aucun ancrage littéral

Rien ne vérifie qu'un nom de table renvoyé par le modèle apparaît réellement
dans le fragment. C'est la vérification la moins chère et la plus rentable du
projet (§5.8), et elle manque.

---

## 8. En une phrase

L'architecture est saine et son axe anti-hallucination est le bon ; ce qui lui
manque n'est pas de la conception mais **du contact avec les données réelles** —
et deux questions posées à temps (accès à la base ? quel serveur d'inférence ?)
pourraient en supprimer la moitié.

---

## Annexe A — La juxtaposition multi-sources, en détail

Développement de la §3.2. C'est le chantier de conception le plus important du
projet, parce qu'il touche à la crainte fondatrice : **une partie du corpus est
ancienne, et le pipeline actuel n'en sait rien.**

### A.1 — Ce que la fusion fabrique exactement

`reconcile` fusionne par nom normalisé sans jamais regarder _quand_ chaque
source a été écrite. Avec `modele_2019.docx` et `specs_2024.xlsx` :

```
2019 :  BALANCE   NUMERIC        nullable      LEGACY_FLAG  CHAR(1)
2024 :  BALANCE   DECIMAL(18,2)  NOT NULL      IBAN         VARCHAR(34)

fusion : BALANCE      NUMERIC        (← _first(), ordre alphabétique des fichiers)
         LEGACY_FLAG  CHAR(1)
         IBAN         VARCHAR(34)
```

Trois mensonges distincts, de natures différentes :

1. **Un arbitrage déguisé en fait.** `NUMERIC` l'emporte parce que
   `modele_2019.docx` passe avant `specs_2024.xlsx` dans l'ordre de parcours du
   répertoire. La valeur affichée est le produit d'un tri alphabétique,
   présentée comme une donnée.
2. **Une union temporelle impossible.** La table rendue contient `LEGACY_FLAG`
   _et_ `IBAN` — un état du système qui n'a peut-être jamais existé à aucun
   moment de sa vie.
3. **Une perte d'auditabilité.** L'expert qui relit ne peut pas savoir que
   `NUMERIC` vient d'un document de sept ans. L'information qui lui aurait permis
   de trancher en trois secondes a été détruite par l'étape censée l'aider.

Le troisième est le plus grave, parce qu'il est invisible. Les deux premiers
produisent une erreur qu'on peut repérer en relisant ; le troisième retire les
moyens de la repérer.

### A.2 — Le piège vicieux : l'absence n'est pas une divergence

`BALANCE` a deux valeurs distinctes → conflit détecté, remonté, `confidence:
low`. Le mécanisme fonctionne.

`LEGACY_FLAG` n'apparaît **que** dans le document de 2019. Aucun conflit n'est
détecté — il n'y a qu'une seule valeur, donc `field_conflict` retourne `None`.
La colonne entre dans le modèle et s'affiche dans le tableau **sans le moindre
signal**. Or il y a trois lectures possibles, et le pipeline en choisit une sans
le dire :

| Lecture                                           | Conséquence sur la fiche                   |
| ------------------------------------------------- | ------------------------------------------ |
| La colonne a été **supprimée** entre 2019 et 2024 | La fiche affirme qu'elle existe → **faux** |
| Le document de 2024 est **partiel**               | La fiche a raison, par chance              |
| Le document de 2024 couvre un **autre périmètre** | Indéterminé                                |

Ces cas sont indistinguables — **sauf si l'on sait ce que chaque source prétend
couvrir.** C'est le concept qui débloque tout le raisonnement :

> Une source qui **énumère** les colonnes d'une table (« Colonnes de ACCOUNT :
> … ») fait une assertion en **monde clos** : à sa date, ce qui n'y figure pas
> n'existe pas.
> Une source qui **mentionne** une colonne au fil du texte fait une assertion en
> **monde ouvert** : elle ne dit rien des autres.

Et c'est une question fermée, donc facile même pour un petit modèle. Une ligne à
ajouter au prompt d'extraction :

```
« Cet extrait donne-t-il la liste complète des colonnes de cette table,
  ou en mentionne-t-il seulement certaines ? »   →   exhaustive | partielle
```

Avec ce seul champ, le sort de `LEGACY_FLAG` devient inférable : _présente dans
une énumération de 2019, absente d'une énumération de 2024 → probablement
supprimée, à confirmer_. Sans lui, la question est indécidable et le pipeline
masque le fait qu'elle se pose.

### A.3 — Dater les sources est plus dur qu'il n'y paraît

Le `mtime` d'un fichier sur un partage réseau ne vaut rien : une copie, une
migration de serveur, quelqu'un qui ouvre un `.xlsx` et le re-sauve par réflexe,
et 2019 devient 2026. Il faut une cascade de signaux, chacun avec sa fiabilité :

| Signal                                                                              | Fiabilité                                   |
| ----------------------------------------------------------------------------------- | ------------------------------------------- |
| Date écrite **dans** le document (page de garde, pied de page, cellule « version ») | haute                                       |
| Métadonnées internes du format (`docProps/core.xml` → `dcterms:created`)            | moyenne — survit aux copies, pas aux resave |
| Nom de fichier (`specs_v3_2024.xlsx`, `_v2_`, `_final_2019`)                        | moyenne                                     |
| `mtime` du système de fichiers                                                      | **basse**                                   |
| Saisie manuelle dans `config.toml`                                                  | **la meilleure**                            |

En pratique : une passe automatique qui _propose_ une date et son niveau de
confiance, et une table dans `config.toml` où l'on corrige à la main les sources
qui comptent. Sur vingt fichiers, c'est un quart d'heure de saisie — et ça vaut
plus que n'importe quel raffinement de prompt.

Corollaire à ne pas manquer : **`date_confidence` doit remonter jusqu'à la
fiche.** Un arbitrage temporel fondé sur un `mtime` n'a pas le même poids qu'un
arbitrage fondé sur une date de page de garde, et le lecteur doit pouvoir le
voir.

### A.4 — Date ≠ autorité : deux axes, pas un

Le plus récent n'est pas le plus juste. Une synthèse PowerPoint de 2025 préparée
pour un comité vaut moins qu'une spécification technique de 2019 écrite par
l'architecte du système. Ce sont deux dimensions indépendantes, et toutes les
deux se déclarent :

```toml
[[sources]]
file      = "specs_v3.xlsx"
date      = "2024-03"
authority = 3            # spécification technique de référence

[[sources]]
file      = "modele_donnees.docx"
date      = "2019-11"
authority = 2            # doc de conception, ancien mais sérieux

[[sources]]
file      = "presentation_comite.pptx"
date      = "2025-06"
authority = 1            # plus récent, mais indicatif
```

Règle de préséance : **autorité d'abord, date ensuite.** Et surtout : la
préséance ne sert qu'à _ordonner l'affichage et suggérer un arbitrage_. Elle ne
supprime jamais l'information concurrente — sinon on retombe exactement dans le
problème du `_first()`, avec un tri plus intelligent mais tout aussi silencieux.

### A.5 — Le modèle de données

Le changement conceptuel tient en une phrase : **on ne stocke plus une colonne,
on stocke des assertions sur une colonne.**

```python
class SourceRef(BaseModel):
    file: str
    locator: str = ""
    date: date | None = None
    date_confidence: str = "unknown"   # explicit|metadata|filename|mtime|unknown
    authority: int = 0
    scope: str = "partial"             # exhaustive|partial  (cf. A.2)


class ColumnAssertion(Column):
    """Une colonne TELLE QUE DÉCRITE PAR UNE SOURCE. Jamais fusionnée."""
    source_ref: SourceRef


class ColumnView(BaseModel):
    """Toutes les assertions sur une même colonne, et leur statut d'accord."""
    name: str
    assertions: list[ColumnAssertion]
    agreement: str   # unanimous | divergent | single_source
    status: str      # current | superseded | possibly_removed
```

`reconcile` ne calcule plus **une valeur**, il calcule **un statut d'accord**.
`_first()` disparaît, et avec lui l'arbitrage silencieux du §7.1.

### A.6 — Le rendu : un seul tableau, seul le désaccord est développé

La §3.2 évoquait une juxtaposition intégrale — un tableau par source. C'est
juste sur le principe et **mauvais en pratique** : dès que trois sources
concordantes décrivent la même table, la fiche triple de longueur sans rien
apprendre, et l'expert décroche. La lisibilité est une propriété de sûreté ici :
une fiche que personne ne lit ne protège de rien.

La bonne forme est un tableau unique, avec trois régimes visuels :

```markdown
## Colonnes

| Colonne       | Type                               | Null             | Clé | Sources                                                    |
| ------------- | ---------------------------------- | ---------------- | --- | ---------------------------------------------------------- |
| `ACCOUNT_ID`  | `BIGINT`                           | non              | PK  | ✅ 3 sources concordantes                                  |
| `BALANCE`     | ⚠️ `DECIMAL(18,2)` **/** `NUMERIC` | ⚠️ non **/** oui |     | ⚠️ divergent                                               |
| `IBAN`        | `VARCHAR(34)`                      | oui              |     | specs_v3 (2024) uniquement                                 |
| `LEGACY_FLAG` | `CHAR(1)`                          | oui              |     | ⏳ 2019 seule — **absente de la liste exhaustive de 2024** |

### ⚠️ BALANCE — divergence entre sources

- `DECIMAL(18,2)`, NOT NULL — **specs_v3.xlsx** » Comptes
  _(2024-03, date explicite, autorité 3)_
- `NUMERIC`, nullable — modele*donnees.docx » §4.2
  *(2019-11, date explicite, autorité 2)\_

→ La source la plus récente **et** la plus autoritaire donne `DECIMAL(18,2)`.
**Non tranché automatiquement.**

### ⏳ LEGACY_FLAG — probablement supprimée

Décrite dans modele_donnees.docx (2019-11). La liste de colonnes de
specs_v3.xlsx (2024-03), donnée comme exhaustive, ne la mentionne pas.
**À confirmer par un expert.**
```

Trois régimes, trois messages distincts :

| Régime              | Signal                                  | Coût de lecture                          |
| ------------------- | --------------------------------------- | ---------------------------------------- |
| Concordance         | `✅ n sources`                          | nul — aussi compact qu'aujourd'hui       |
| Divergence          | valeurs côte à côte + section de détail | payé là où il y a une décision à prendre |
| Absence asymétrique | `⏳` + explication du raisonnement      | payé là où il y a un doute réel          |

Le coût de lisibilité n'est payé qu'aux endroits qui portent une décision. Et
surtout : **l'expert peut trancher sans ouvrir un seul fichier source** — ce qui
est exactement la valeur que le projet cherche à produire (§5.2).

### A.7 — Ce que ça coûte

|              |                                                                                                                                                                                                                                       |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Ajouté**   | 4 champs sur `SourceRef` (~10 l.) · passe de datation multi-signaux (~80 l.) · champ `scope` dans le prompt d'extraction (2 l.) · calcul du statut d'accord dans `reconcile` (~60 l.) · les 3 régimes dans le template Jinja (~40 l.) |
| **Supprimé** | `_first()` et son arbitrage silencieux · l'essentiel de `conflicts.py` — le désaccord n'est plus un cas particulier détecté après coup, c'est le **régime normal** du modèle                                                          |
| **Solde**    | à peu près neutre en volume de code, **très positif en garanties**                                                                                                                                                                    |

Le vrai coût n'est pas le code : c'est la datation manuelle des sources, un
quart d'heure à refaire quand le corpus change. C'est le meilleur rapport
effort / fiabilité de tout ce document.

### A.8 — La limite de ce design

Il suppose que les documents décrivent **le même système à des dates
différentes**. Si certains décrivent des systèmes ou des environnements
différents — prod / recette, ou deux applications qui partagent des noms de
tables génériques (`CLIENT`, `PARAMETRE`, `HISTORIQUE`) — l'axe temporel ne
suffit plus : deux valeurs divergentes ne sont alors pas un conflit à trancher
mais **deux faits vrais simultanément**, et les présenter comme une divergence
est une nouvelle façon de mentir.

Il faudrait alors un axe `système` ou `environnement` en plus, et un découpage
différent : non plus une fiche par table, mais une fiche par couple
(système, table). À vérifier dès qu'on aura les vrais fichiers sous les yeux —
c'est typiquement le genre de chose qu'on découvre en ouvrant le troisième
document, et qui invalide une semaine de travail si on ne l'a pas cherché.
