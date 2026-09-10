# docmaker — todolist

Dernière mise à jour : 2026-09-11.
Doc de référence : `couche-semantique.md` (le quoi et le pourquoi). Celui-ci est
le **quoi faire, dans quel ordre**.

**Conventions**

- `[ ]` à faire · `[x]` fait · `[~]` en cours
- 👤 demande une personne (pas du code) — à lancer tôt, ça prend du temps social
- ⛔ bloqué : la dépendance est nommée dans l'item
- Chaque item porte une **sortie** (l'artefact produit) et un **critère** (comment
  on sait que c'est fini). Sans critère, l'item n'est pas prêt à être pris.

---

## Phase 0 — Reconnaissance · rien ne la bloque

L'étape `recon` est écrite (`docmaker/pipeline/recon.py`). Elle ne fait aucun
appel LLM, aucune écriture en base, aucune lecture de donnée métier.

- [ ] **Installer les dépendances**
  `poetry lock && poetry install`
  _Sortie :_ `oracledb` et `sqlglot` dans l'environnement.
  _Critère :_ `poetry run python -c "import oracledb, sqlglot"` passe.

- [ ] **Renseigner l'accès**
  `dsn`, `user` dans `[oracle]` de `config.toml` ; `ORACLE_PASSWORD` dans `.env`.
  _Critère :_ `poetry run python -c "from docmaker.config import load_settings; load_settings()"` passe.

- [ ] **Lancer la reconnaissance**
  Mettre `stages = ["recon"]` dans `config.toml`, puis `poetry run docmaker`.
  _Sortie :_ `build/recon.json` et `build/recon.md`.
  _Critère :_ le rapport contient les 8 sections sans « aucune donnée » massif.

- [ ] **Figer le périmètre**
  Lire la §1 du rapport, reporter les schémas retenus dans `owners` de
  `config.toml` (la découverte automatique ratisse trop large).
  _Critère :_ un second passage donne les mêmes chiffres sur un périmètre réduit.

- [ ] **Décider à partir des chiffres** — les cinq verdicts que le rapport rend :

  | Section | Chiffre | Ce qu'il décide |
  | --- | --- | --- |
  | §2 | Taux de `COMMENT ON` | Élevé → collecter avant de générer. Nul → chantier complet |
  | §4 | Taux de colonnes analysées | Élevé → profiling gratuit. Faible → scans à écrire |
  | §3 | Nombre de contraintes `R` | Faible (attendu) → construire le test d'inclusion |
  | §5 | Part « angle mort » | Grande → le lineage SQL ne suffira pas, il faut tracer les chargements externes |
  | §6 | **Taux de parsing `sqlglot`** | > 85 % → lineage viable · < 50 % → prévoir une extraction dégradée |

---

## Phase 1 — Demandes à lancer en parallèle 👤

À faire **maintenant**, pendant la phase 0 : ces choses s'obtiennent en jours ou
en semaines, pas en minutes.

- [ ] 👤 **Obtenir des classeurs Tableau** (`.twb` / `.twbx`)
  Commencer par quelques fichiers des tableaux de bord les plus consultés :
  aucun droit particulier, parsing XML immédiat. Voir `couche-semantique.md` §2.8.
  _Critère :_ au moins 3 classeurs représentatifs en local.

- [ ] 👤 **Demander l'accès à l'API Metadata de Tableau Server** (GraphQL)
  Plus riche : lineage jusqu'à la colonne sur tout le parc publié, plus les
  statistiques de consultation — le meilleur substitut aux logs de requêtes.
  _Critère :_ un token qui répond à une requête GraphQL de test.

- [ ] 👤 **Identifier comment tournent les traitements hors base**
  Ordonnanceur externe, scripts sur un serveur, chargements par fichiers ?
  Répond à la §5 du rapport de reconnaissance par le versant humain.
  _Sortie :_ quelques lignes dans `couche-semantique.md` §5.3.

- [ ] 👤 **Trouver un interlocuteur qui connaît le datamart**
  Une heure de son temps, une seule fois, pour la validation de la phase 3.
  Ne rien lui demander d'écrire — voir phase 3.

- [ ] 👤 **Demander `SELECT_CATALOG_ROLE`** (facultatif, peu coûteux)
  Donne accès à `V$SQL` — le cache de curseurs, sans AWR ni licence Diagnostic
  Pack. Échantillon de requêtes réelles à coût nul. Demande étroite, distincte
  d'un « accès aux logs ».

---

## Phase 2 — Le socle déterministe

Ordre choisi pour que chaque étape ait de la valeur seule, et que les premières
ne dépendent d'aucun accès supplémentaire.

- [ ] **Étape `catalog`** — lecture du catalogue Oracle → squelette factuel
  Tables, vues, vues matérialisées, colonnes, types, `NOT NULL`, PK, FK, index,
  partitions, synonymes, dépendances, **et les `COMMENT ON` existants**.
  _Spécification :_ `exemples/catalog.example.json` — format exact attendu.
  _Conception :_ `couche-semantique.md` §6.1 (fidélité au dialecte, requêtes en
  masse, sérialisation déterministe).
  _Sortie :_ `build/catalog.json` ; modèles Pydantic dans `models.py`.
  _Critères :_
  - le nombre de colonnes correspond exactement à la §1 du rapport `recon` ;
  - zéro appel LLM, zéro écriture en base ;
  - deux exécutions successives produisent un fichier **identique à l'octet**
    (sérialisation déterministe → le diff mensuel devient une veille de dérive) ;
  - tout objet ayant un `sql` non nul est prêt à être passé à `lineage`.

- [ ] **Étape `glossary`** — dictionnaire d'abréviations
  Découper tous les noms sur `_` et sur les frontières de casse, compter les
  tokens par fréquence, exporter les 150 plus fréquents pour validation.
  _Sortie :_ `build/tokens.json` + `out/glossaire.md` (à faire relire).
  _Critère :_ les 150 premiers tokens couvrent > 80 % des occurrences.
  - [ ] 👤 Faire valider le glossaire (une heure, une personne)

- [ ] **Étape `lineage`** — parsing des vues, MV et PL/SQL
  ⛔ Dépend du verdict §6 de la reconnaissance.
  1. `ALL_DEPENDENCIES` d'abord : graphe table à table, gratuit, sans parsing.
  2. `sqlglot` ensuite pour descendre au niveau colonne (expression de calcul).
  3. Extraire au passage le graphe de jointures et sa fréquence.
  _Sortie :_ `build/lineage.json` — par colonne : expression, colonnes amont,
  objet source ; plus le graphe de jointures pondéré.
  _Critère :_ pour 10 colonnes dérivées tirées au hasard, l'expression remontée
  correspond au texte de la vue, vérifié à la main.
  - [ ] Marquer explicitement la part non parsée — ne jamais laisser croire à
        une couverture complète.

- [ ] **Étape `profile`** — domaines de valeurs
  1. D'abord `ALL_TAB_COL_STATISTICS` et `ALL_TAB_HISTOGRAMS` : gratuit, aucun
     scan. Décoder les `RAW` avec `DBMS_STATS.CONVERT_RAW_VALUE`.
  2. Scans `GROUP BY` ciblés seulement là où les stats manquent ou sont périmées,
     et uniquement sur le périmètre prioritaire.
  _Sortie :_ `build/profile.json` — par colonne : distincts, nulls, min/max, top-N.
  _Critère :_ les colonnes de code du périmètre prioritaire ont toutes un domaine
  de valeurs, ou une raison explicite de ne pas en avoir.

- [ ] **Étape `joins`** — retrouver les clés étrangères non déclarées
  Test d'inclusion `FACT.X ⊆ DIM.X` sur les couples candidats (nom identique ou
  proche, type et cardinalité compatibles), croisé avec les jointures observées
  dans le lineage.
  _Sortie :_ `build/joins.json` — chaque arête avec son statut : déclarée,
  observée dans le SQL, vérifiée par inclusion.
  _Critère :_ chaque table de fait du périmètre prioritaire a au moins un chemin
  vers ses dimensions, marqué du niveau de preuve.

- [ ] **Étape `tableau`** — parsing des classeurs
  ⛔ Dépend de la phase 1. Extraire : `caption` (libellés humains ↔ colonnes
  physiques), champs calculés (définitions de métriques), jointures des sources,
  membres de filtres (dictionnaires de valeurs), titres de feuilles (questions).
  _Sortie :_ `build/tableau.json`.
  _Critère :_ chaque `caption` extrait est rattaché à une colonne existante du
  `catalog`, ou signalé comme non résolu.

- [ ] **Priorisation** — le classement qui remplace les logs
  Combiner : fan-out `ALL_DEPENDENCIES`, `ALL_TAB_PRIVS`, `ALL_TAB_MODIFICATIONS`,
  `NUM_ROWS`, et surtout la couverture Tableau.
  _Sortie :_ `build/priority.json` — un score par table.
  _Critère :_ le top 50 est jugé plausible par l'interlocuteur métier.

---

## Phase 3 — Le jeu d'évaluation ⚠ le manque le plus sérieux

Rien ne dit aujourd'hui **quand c'est bon**. Cet item précède les questions
d'architecture qui restent.

- [ ] **Constituer 20 à 30 brouillons de paires question → SQL**
  1. Extraire d'abord depuis Tableau : un titre de feuille est une question, la
     requête derrière est sa réponse, en production, regardée tous les jours.
  2. Compléter depuis le schéma pour couvrir les trous.
  _Sortie :_ `eval/questions.yaml`.

- [ ] **Auto-vérifier ce qui est vérifiable seul**
  Ce qu'on peut contrôler sans personne : la jointure fait-elle exploser le
  nombre de lignes, la clé est-elle unique (`COUNT(DISTINCT) = COUNT(*)`), les
  volumes sont-ils plausibles, deux calculs de la même métrique concordent-ils.
  _Critère :_ toutes les requêtes tournent et rendent un résultat non vide.

- [ ] 👤 **Faire corriger, pas écrire**
  Demander « corrige-moi ces 30 brouillons », jamais « écris-moi 30 questions ».
  Relire coûte dix fois moins cher qu'écrire. Une heure suffit.

- [ ] **Consigner chaque correction comme un artefact de première classe**
  Un « non, pour le CA on exclut `TYP_MVT = 'ANN'` sinon tu comptes les
  annulations » vaut plus que cinquante descriptions de colonnes, et n'est écrit
  nulle part ailleurs. C'est exactement la connaissance tacite que le projet
  existe pour capturer.
  _Sortie :_ `eval/pieges.md`.

- [ ] **Compléter la couverture**
  Le jeu doit contenir : filtre simple · agrégation · jointure multi-tables ·
  fenêtre temporelle · métrique dérivée · **2-3 pièges connus** où le SQL naïf
  est faux · **2-3 questions sans réponse possible**, pour vérifier que le
  système refuse au lieu d'inventer. En bancaire, cette dernière catégorie n'est
  pas un raffinement.

- [ ] **Écrire le harnais d'évaluation**
  _Critère :_ une commande rend un taux de réussite reproductible, comparable
  avant / après chaque modification de la couche sémantique.

---

## Phase 4 — L'annotation LLM

⛔ Ne pas commencer avant la phase 2 : le LLM doit **nommer**, pas construire, et
il ne peut nommer qu'ancré sur des preuves.

- [ ] **Étape `annotate`**
  Un appel par colonne du périmètre prioritaire, avec en contexte : l'expression
  de calcul (lineage), les valeurs observées (profile), les tokens décodés
  (glossary), le `caption` Tableau s'il existe, le `COMMENT ON` s'il existe, et
  les passages de documentation rattachés.
  _Critère :_ « preuve insuffisante » est une réponse acceptable et effectivement
  rendue quand c'est le cas — mesurer ce taux, un taux nul est un signal d'alarme.

- [ ] **Adapter `extract`** aux documents Office
  Vocabulaire fermé issu du `catalog` : rattacher une phrase à une table connue,
  jamais générer un nom de table.
  _Critère :_ aucune entité produite qui n'existe pas dans le catalogue.

- [ ] **Réduire `reconcile` et `conflicts`**
  Le catalogue tranche, les documents annotent. Une contradiction sur un type
  n'est plus un conflit à arbitrer : c'est un document qui a tort.

- [ ] **Mesurer** l'annotation sur le jeu de la phase 3, avant / après.

---

## Phase 5 — Sorties

- [ ] **`render` multi-backend depuis `model.json`**
  Markdown (humain) · `chunks.jsonl` (RAG) · payload OpenMetadata.
  Le pivot reste `model.json` : source unique dont tout dérive.

- [ ] **Backend OpenMetadata**
  Mapper vers `CreateTableRequest` : FQN `service.database.schema.table`, types
  normalisés vers l'enum OpenMetadata, `review_status` / `confidence` / sources
  en custom properties, lineage en relations.
  ⛔ Dépend de : OpenMetadata est-il déployé, alimenté, par quel connecteur ?

- [ ] **Livrable text-to-SQL**
  Graphe de jointures avec chemin canonique · dictionnaires de valeurs ·
  définitions de métriques avec leur maille · exemples few-shot issus de la
  phase 3. Voir `couche-semantique.md` §1.

---

## Décisions en attente

- [ ] **Le modèle qui fait le text-to-SQL final** — la contrainte ≤ 30B on-prem
      s'applique-t-elle à lui, ou seulement au pipeline de construction ?
- [ ] **Où vit la couche sémantique** — OpenMetadata, un dépôt de fichiers, un
      semantic model type dbt ?
- [ ] **Rythme de regénération** — le datamart bouge ; à quelle fréquence
      rejouer, et que devient une annotation validée à la main quand la colonne
      change ?

---

## Dette connue

- [ ] **Le pipeline documentaire n'a jamais tourné sur un document réel.**
      `samples/` est vide. Reste le fait le plus important du projet
      (`prise-de-recul.md` §1).
- [x] **`docmaker/extract.py` en double** — supprimé.
- [x] **§7.1 — arbitrage silencieux dans le tableau rendu.** `MergedColumn`
      porte désormais ses divergences ; la cellule affiche `⚠ A / B` au lieu
      d'une valeur choisie par ordre alphabétique de fichier.
- [x] **§7.2 — un run long pouvait tout perdre.** `extract` écrit dans
      `facts.jsonl` fragment par fragment, reprend où il s'est arrêté, et
      n'abandonne plus le run entier sur une erreur réseau.
- [x] **§7.3 — faux conflits sur les types.** Normalisation avant comparaison
      (espaces, casse, précision, alias). `NUMBER` et `DECIMAL` restent
      distincts : les conflater masquerait une différence réelle.
- [x] **§7.4 — le conflit `nullable` perdait sa provenance.** Les trois champs
      comparés passent maintenant par le même chemin.
- [ ] **§7.5 — le chunking repose entièrement sur des titres Markdown.**
      Non corrigé : ne se vérifie qu'au premier run sur un `.xlsx` réel.
- [ ] **`prise-de-recul.md` §5** liste d'autres défauts qui restent valables
      tant que ces étapes existent sous cette forme.
