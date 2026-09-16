# Critique de la stratégie « OpenMetadata → semantic layer » et plan d'implémentation

Date : 2026-09-14. Répond à une proposition de stratégie calquée sur l'architecture
Snowflake (Semantic Views + Cortex Analyst). Complète `couche-semantique.md` (le
quoi et le pourquoi) et `todo.md` (l'ordre des tâches) ; ce document tranche
**l'architecture** et donne le plan jusqu'à la mise en service.

---

## 1. Ce que la stratégie proposée a de juste

Six points sont solides et sont repris tels quels dans le plan.

**La séparation en trois couches** — métadonnées physiques / modèle sémantique /
runtime text-to-SQL. C'est la bonne lecture de Snowflake, et elle recoupe déjà le
§8 de `couche-semantique.md`.

**Le refus du « tout vectoriser ».** Transformer le catalogue en markdown, le
découper et l'enfourner dans une base vectorielle détruit précisément la structure
qui fait la valeur. Garder un graphe sémantique typé est le bon choix, et il est à
contre-courant de ce que fait la majorité des projets text-to-SQL.

**Les métriques explicites plutôt que déduites.** C'est le levier le plus rentable
sous contrainte ≤ 30B : le modèle ne doit jamais décider seul que « clients
actifs » vaut `COUNT(CLIENT_ID)`.

**Le literal retrieval** — « Suisse romande » → `'CH-ROM'`. Point réellement
sous-estimé, absent de la plupart des architectures naïves, et correctement
identifié ici.

**Documents → métadonnées structurées plutôt que documents → RAG.** C'est exactement
le pivot que ce projet a déjà fait le 2026-09-10. La proposition le redécouvre et
le confirme, ce qui est un bon signe.

**Le dimensionnement final** — 5-15 entités, ~10 métriques, 20-50 requêtes vérifiées,
mesurer avant de construire une plateforme. Sobre et juste.

---

## 2. Six objections

### 2.1 — Le runtime proposé annule le gain architectural déjà acquis

C'est l'objection principale.

Le schéma de runtime proposé se termine par :

```
semantic subgraph → LLM → candidate SQL → checks → Oracle
```

Le LLM **écrit le SQL**. Or le §8.5 de `couche-semantique.md` a tranché l'inverse,
et pour une raison qui n'a pas été réfutée : le mauvais chemin de jointure et le
fan-out sont les deux premières causes de réponse fausse **silencieuse**, et un
moteur qui compile les fait sortir du champ de responsabilité du modèle. Le LLM
n'exprime plus une jointure, donc il ne peut plus la rater.

Revenir à la génération de SQL réintroduit ces deux classes d'erreur. Sous
contrainte ≤ 30B on-prem, c'est le pire moment pour les réintroduire.

Nuance honnête : Snowflake fait ce choix avec des modèles bien plus gros, et ça
marche pour eux. Le raisonnement ne se transpose pas à 30B.

**Position retenue :** deux régimes (§5), pas un choix exclusif — mais le régime
compilé est le régime par défaut, pas le régime de repli.

### 2.2 — OpenMetadata n'est pas un store sémantique, c'est une interface de revue

La proposition fait d'OMD la source de vérité métier, y compris via des custom
properties (`semantic_role`, `grain`, `ai_notes`). Trois problèmes :

- **Les custom properties sont des chaînes non validées.** Aucun typage, aucune
  compilation, aucune garantie de cohérence. Y mettre le grain d'une table, c'est
  écrire un contrat dans un champ texte libre.
- **OMD n'a pas de modèle de métrique.** La proposition l'admet à demi-mot en
  gardant metrics, verified queries et graphe sémantique en dehors. Mais alors OMD
  et le modèle sémantique portent tous deux la description de colonne : **qui gagne
  en cas de divergence ?** La proposition ne le dit pas.
- Ce projet a déjà une meilleure réponse, écrite et testée : `src/sink/omd.py`.
  OMD est le lieu de la **validation humaine** (`state=Suggested`, tag « IA – à
  valider », règle `human_touched` qui n'écrase jamais une correction humaine,
  `lastSourceHash` pour l'idempotence). Le pipeline est la source des propositions,
  OMD est la file d'attente de revue.

**Position retenue :** OMD reste l'interface humaine et le catalogue. Le pivot
sémantique est un fichier versionné. Ce qui manque n'est pas plus d'OMD, c'est la
**boucle de retour** OMD → pivot (§6.3), qui n'existe pas aujourd'hui.

### 2.3 — L'étape « construire les entités candidates » est traitée en cinq lignes et coûte des semaines

Regrouper `CLIENT`, `CLIENT_ADDRESS`, `CLIENT_SEGMENT`, `CLIENT_HISTORY` en une
entité `Customer` : « proposé par LLM mais validé humainement ». Sur un datamart
de plusieurs centaines de tables, cette phrase cache le poste le plus coûteux du
chantier.

Pire : **c'est peut-être un faux problème ici.** Le regroupement multi-tables n'est
nécessaire que si le modèle physique est normalisé. Ce datamart est déjà en étoile
(`DMT_DIM_CLI`, `DMT_F_CRD_OCT`, `DMT_CPT_MVT_J`) — l'entité logique coïncide
largement avec la table de dimension. Et le regroupement par préfixe de nom est
précisément le pari fragile que `couche-semantique.md` §2.1 a écarté.

**Position retenue :** ne pas écrire de « semantic model builder » générique.
Partir des conventions de nommage déjà présentes (`_DIM_`, `_F_`, `_D_`, `_H_`),
mesurer combien de tables elles couvrent, et ne traiter à la main que le reste.
Le banc d'essai permet de chiffrer ça avant d'écrire une ligne de code.

### 2.4 — Rien sur la maintenance

La proposition décrit une construction one-shot. La question 3 des « décisions en
attente » de `todo.md` reste entière : **que devient une annotation validée à la
main quand la colonne change ?**

C'est la question qui décide si le livrable est vivant ou s'il pourrit en six mois.
Elle a une réponse (§6), elle s'appuie sur du code déjà écrit, et elle est absente
de la stratégie proposée.

### 2.5 — Les requêtes vérifiées servent deux fois, ce qui invalide la mesure

La proposition utilise le même jeu de paires question → SQL comme **exemples
few-shot** et comme **critère d'évaluation**. C'est une fuite : le système est
évalué sur des exemples qu'on lui a donnés.

**Position retenue :** séparation stricte. Les 50 questions de
`openmetadata/database/eval/questions.yaml` se partagent en 30 (few-shot,
priorisation, révélateur de manques) et 20 (test, jamais dans un prompt). Le taux
publié est celui des 20.

### 2.6 — Le retrieval sémantique et la base vectorielle sont prématurés

Deux mécanismes de retrieval sont proposés : élagage du modèle sémantique par
embeddings, et recherche de literals dans Qdrant/pgvector.

Sur **un seul** datamart, le modèle sémantique élagué aux tables qui comptent tient
dans 32 000 tokens. Ajouter un retrieval, c'est ajouter une cause de réponse
impossible (le bon fait n'est pas retrouvé) pour résoudre un problème de volume
qu'on n'a pas encore mesuré.

Pour les literals, `couche-semantique.md` §2.3 est plus précis que la proposition :
`ALL_TAB_HISTOGRAMS` livre le domaine **complet** des colonnes de faible
cardinalité — exactement les colonnes de code. Ces dictionnaires tiennent dans le
prompt. Une base vectorielle ne sert que pour la haute cardinalité (noms de clients,
libellés de produits), un cas à traiter quand il se présente.

**Position retenue :** zéro base vectorielle au départ. Réévaluer sur mesure.

---

## 3. Ce qui manque et qui est déterminant

Quatre éléments absents de la proposition, tous mécaniquement calculables, tous à
fort rendement.

### 3.1 — Les filtres par défaut

Dans ce datamart, les tables `ODS_H_*` sont historisées SCD2 et portent `FLG_ACT`.
Un SQL qui oublie `FLG_ACT = 1` compte plusieurs fois le même client. La requête
est syntaxiquement correcte, s'exécute, rend un nombre — et il est faux.

Aucune description de colonne n'empêche ça. Un **filtre par défaut attaché à
l'entité et appliqué par le compilateur** l'empêche par construction. C'est
probablement l'ajout le plus rentable de tout le dispositif, et il ne coûte rien.

### 3.2 — La maille, déterminée mécaniquement

La proposition cite « grain » dans une liste sans jamais dire comment l'obtenir.
C'est une requête : `COUNT(*) = COUNT(DISTINCT clé)` ? Le piège P03 du ground truth
en donne l'illustration exacte : `DMT_CPT_MVT_J` a un suffixe `_J` qui suggère un
agrégat journalier, alors que la table est au grain mouvement unitaire. Le nom
ment, la requête ne ment pas.

### 3.3 — La cardinalité des jointures, également mécanique

C'est ce qui permet au moteur de gérer le fan-out. Elle se déduit du test
d'unicité de chaque côté de l'arête, à l'étape `joins` déjà prévue.

### 3.4 — Le refus

En bancaire, un système qui répond faux avec assurance est plus dangereux qu'un
système qui refuse. Il n'y a aucun mécanisme d'abstention dans la proposition. Le
jeu d'évaluation prévoit déjà 2-3 questions sans réponse possible : le taux
d'abstention correcte est une métrique de premier rang, pas un raffinement.

---

## 4. Ce qui se simplifie

| Proposé | Retenu | Raison |
| --- | --- | --- |
| Base vectorielle pour le modèle sémantique | Rien | Un seul datamart, tient dans le contexte |
| Base vectorielle pour les literals | Dictionnaires inline | `ALL_TAB_HISTOGRAMS` donne le domaine complet |
| Semantic model builder générique | Conventions de nommage + reste à la main | Le datamart est déjà en étoile |
| YAML de spécification Snowflake | `model.json` → projection Cube | Le YAML Snowflake n'est compilable que par Snowflake |
| 8 étapes séquentielles | 3 boucles de temporalités différentes | La maintenance n'est pas une étape, c'est un régime |
| OMD comme store sémantique | OMD comme file de revue | Pas de typage, pas de compilation |

---

## 5. L'architecture retenue

```
Oracle (lecture seule)
   │
   ├── recon ──────► build/recon.json      dimensionnement, verdicts
   ├── catalog ────► build/catalog.json    squelette, sérialisation déterministe
   ├── lineage ────► build/lineage.json    expressions, graphe de flux
   ├── profile ────► build/profile.json    domaines de valeurs
   ├── joins ──────► build/joins.json      arêtes + cardinalité + niveau de preuve
   └── grain ──────┐
                   │
   Tableau ────────┤
   Documents ──────┤
                   ▼
            model.json  ◄──── boucle de retour OMD (validations humaines)
            (pivot neutre, versionné git)
                   │
      ┌────────────┼────────────┬──────────────┐
      ▼            ▼            ▼              ▼
  Proposals    modèle Cube   contexte LLM   harnais d'éval
  → OMD        (régime A)    (régime B)     (50 questions)
  (revue)
```

**Deux régimes à l'exécution, un seul validateur.**

*Régime A — compilé.* La question est traduite en (dimensions, mesures, filtres),
Cube compile en SQL. Le chemin de jointure et le fan-out sortent du champ du
modèle. Régime par défaut.

*Régime B — généré.* Pour les questions hors modèle. Le LLM écrit du SQL, et le
validateur déterministe tranche.

*Le validateur, commun aux deux régimes :*

1. parse `sqlglot` dialecte `oracle` — échec ⇒ rejet ;
2. **toute table et colonne référencée existe dans `catalog.json`** — sinon rejet.
   C'est l'anti-hallucination le plus efficace du dispositif, et il est gratuit ;
3. toute jointure utilisée est une arête connue de `joins.json` — sinon rejet ;
4. les filtres par défaut des tables touchées sont présents — sinon injection ;
5. `EXPLAIN PLAN` — cardinalité estimée aberrante ⇒ rejet ;
6. exécution avec `FETCH FIRST n ROWS ONLY` et timeout.

Après N échecs : **abstention motivée**, jamais une réponse plausible.

---

## 6. Le plan

### Jalon 0 — Faire tourner ce qui est déjà écrit · rien ne le bloque

C'est l'anomalie la plus coûteuse du projet aujourd'hui : **tout le dispositif de
mesure existe et n'a jamais été exécuté.** `docmaker/pipeline/recon.py` fait 21 ko
et n'a jamais tourné. `openmetadata/database/eval/attendus/` est vide. Les
conteneurs `oracle_bank_dwh` et `openmetadata_server` tournent.

Tant que ce jalon n'est pas franchi, tout arbitrage d'architecture est une opinion.

1. `poetry install`.
2. Charger les données du banc (profil S), puis `eval/run_eval.py` → les 50 attendus
   figés dans `eval/attendus/`. Les packages PL/SQL ne sont pas idempotents :
   `tools/reset_data.py` avant tout rechargement.
3. Lancer `recon` contre `oracle_bank_dwh` avec le compte `BANK_RO`.
4. **Noter le recon contre le ground truth** — le test du testeur. Le rapport
   doit retrouver les deux angles morts connus (`DMT_F_RSQ_TSU` alimentée hors
   base, `PKG_CHG_DMT_RSQ.P_CHG_QUOT` en `EXECUTE IMMEDIATE`) et buter sur le
   parsing au bon endroit.

*Critère :* `build/recon.json` produit ; 50/50 attendus figés ; les deux angles
morts détectés ; les cinq verdicts du §0 de `todo.md` rendus en chiffres.

### Jalon 1 — Unifier les deux arbres de code

`docmaker/` (config.toml, pipeline, recon) et `src/` (proposal, sources, enrich,
sink, eval) ne se parlent pas, ont deux modes de configuration et deux points
d'entrée. Ils vont diverger.

```
docmaker/
  config.py          config.toml reste la seule source de valeurs
  models.py          + les modèles du catalogue
  proposal.py        ← src/proposal.py, contrat commun inchangé
  pipeline/          recon · catalog · lineage · profile · joins · grain
  semantic/          model.json, projections : cube · omd · prompt
  sources/           conventions · tableau · omd_feedback (nouveau, §6.3)
  sink/omd.py        ← src/sink/omd.py, seul point d'écriture
  eval/              coverage (existant) + harnais text-to-SQL (nouveau)
```

*Critère :* un seul point d'entrée, `pytest` vert (les 15 tests existants passent
sans modification de leur logique).

### Jalon 2 — Le socle déterministe, chaque étape notée

La contribution propre de ce plan : le ground truth permet de **noter chaque étape
déterministe séparément**, au lieu de ne juger que le résultat final. On sait alors
où le LLM est vraiment nécessaire, au lieu de le supposer.

| Étape | Sortie | Confrontée à | Métrique |
| --- | --- | --- | --- |
| `catalog` | `catalog.json` | `modele_propre.yaml` | écart nul sur objets et colonnes ; deux exécutions identiques à l'octet |
| `glossary` | `tokens.json` | `glossaire_abreviations.yaml` | % des tokens du GT présents dans le top 150 |
| `lineage` | `lineage.json` | `lineage.yaml` | % des flux par domaine retrouvés ; 2 angles morts signalés |
| `profile` | `profile.json` | `dictionnaires_valeurs.yaml` | % de dictionnaires retrouvés, et par quelle voie (histogramme gratuit vs. scan) |
| `joins` | `joins.json` | `mapping_degrade.yaml` | % des ~30 % de FK supprimées retrouvées par test d'inclusion |
| `grain` | dans `model.json` | `pieges.yaml` P03 | le faux-ami `_J` est-il détecté ? |

Ces six taux disent, chiffres à l'appui, quelle part du sens s'obtient sans LLM.
C'est l'information qui manque à toute la discussion actuelle.

*Critère :* les six lignes renseignées ; `catalog.json` bit-identique sur deux
exécutions (condition de la veille de dérive du §6.1).

### Jalon 3 — La baseline, avant toute couche sémantique

Les 20 questions de test, un modèle ≤ 30B, contexte = DDL brut du périmètre. Taux
de réussite par niveau.

Comparaison **par exécution** contre les attendus figés — jamais par ressemblance
textuelle du SQL. Règle de comparaison à figer maintenant : ensemble de lignes
normalisé (tri, arrondi à 2 décimales, colonnes par position).

*Critère :* un nombre, reproductible. C'est le zéro contre lequel tout gain ultérieur
se mesure. Sans lui, « la couche sémantique améliore les choses » reste une croyance.

### Jalon 4 — La couche sémantique, par tranches mesurées

Ordre dicté par la classe d'erreur retirée, pas par l'élégance. Après chaque tranche,
rejouer les 20 questions de test.

1. **Filtres par défaut** (§3.1) — coût nul, gain attendu élevé.
2. **Dictionnaires de valeurs inline** — toute colonne de cardinalité ≤ 50.
3. **Grain et cardinalité de jointure déclarés** — retire le fan-out.
4. **Chemin de jointure canonique** quand plusieurs existent — le ground truth
   documente le cas : deux chemins vers le client, qui divergent en co-titularité.
5. **Métriques** — les 4 du ground truth, avec leur piège.
6. **Few-shot** — depuis les 30 questions d'entraînement **uniquement**.

*Critère :* un tableau à six lignes, delta par tranche. Une tranche dont le delta
est nul est une tranche à ne pas emporter en production.

### Jalon 5 — Le runtime

Le validateur du §5 d'abord (il vaut seul, indépendamment du régime), puis le
régime A (Cube), puis le routeur.

*Critère :* sur les 20 questions de test, aucun SQL exécuté ne référence une
colonne inexistante ; les questions sans réponse possible produisent une abstention
motivée et non une réponse.

### Jalon 6 — La maintenance : trois boucles

Le point aveugle de la stratégie proposée.

#### 6.1 — Boucle courte, nocturne : la dérive structurelle

`catalog` rejoué, sérialisation déterministe, `git diff catalog.json`. Chaque
différence est classée automatiquement :

| Changement détecté | Conséquence |
| --- | --- |
| colonne ajoutée | annotation à produire |
| colonne supprimée | annotation, métrique et requête vérifiée qui la citent : invalidées |
| type changé | revalidation |
| **expression de lineage changée** | **l'annotation validée devient suspecte** |

La dernière ligne est le cas que personne ne gère. `sink/omd.py` en a déjà la
moitié : `lastSourceHash` détecte que les preuves ont changé. Il manque la
conséquence — quand une annotation `human_touched` repose sur des preuves qui ont
bougé, la règle 1 interdit de l'écraser (c'est bien), mais rien ne signale qu'elle
est périmée. Extension précise et petite : poser un tag « à revalider » sans
toucher au contenu.

#### 6.2 — Boucle moyenne, à chaque modification : la non-régression

Le banc rejoué, question par question, OK/KO avant et après. Une question qui passe
de OK à KO bloque la livraison. C'est ce qui empêche une amélioration moyenne de
masquer trois régressions.

#### 6.3 — Boucle longue, humaine : la file de revue et son retour

Aujourd'hui `sink/omd.py` écrit vers OMD et **rien ne relit**. OMD est un cul-de-sac :
ce qu'un humain corrige dans l'interface n'existe pour personne d'autre.

Le maillon manquant est `sources/omd_feedback.py` :

- description confirmée dans OMD → `confidence = 1.0`, `human_touched = True`
  dans `model.json` ;
- description **corrigée** → la correction devient la vérité, et l'écart
  proposition → correction est consigné.

Ces écarts sont la seule trace de la connaissance tacite que le projet existe pour
capturer. Un « pour le chiffre d'affaires on exclut `TYP_MVT = 'ANN'`, sinon tu
comptes les annulations » vaut plus que cinquante descriptions de colonnes, et
n'est écrit nulle part ailleurs. Destination : `eval/pieges.md`, et à terme les
filtres par défaut du §3.1.

*Critère :* une correction faite dans l'interface OMD se retrouve dans `model.json`
au run suivant, et n'est jamais réécrasée.

---

## 7. Ce qui reste à décider

Inchangé depuis `todo.md`, mais le plan ci-dessus en déplace deux :

- **Le modèle du text-to-SQL final** — la contrainte ≤ 30B s'applique-t-elle à lui ?
  Le jalon 3 rend la question chiffrable au lieu de théorique.
- **Où vit la couche sémantique** — tranché ici : `model.json` versionné, OMD en
  interface de revue, Cube en cible de compilation.
- **Rythme de regénération** — tranché ici : §6.1, nocturne, avec classification
  automatique de l'impact.
- **Le régime A est-il suffisant ?** Ne se répond qu'après le jalon 5 : quelle
  proportion des 20 questions de test tombe hors modèle.

---

## 8. Le risque principal, nommé

Le banc d'essai est un datamart **fictif, construit par ce projet, dont le ground
truth a été généré en même temps que la dégradation**. Il mesure donc en partie la
capacité du pipeline à retrouver ce que le générateur a caché — pas la réalité d'un
datamart bancaire de production.

C'est un excellent banc pour tout ce qui est mécanique (parsing, inclusion,
histogrammes, unicité) : ces propriétés sont vraies indépendamment de qui a écrit
le schéma. C'est un banc **optimiste** pour tout ce qui touche au nommage et aux
conventions, parce que la dégradation appliquée est systématique là où un vrai
datamart est incohérent par sédimentation.

Conséquence pratique : les taux du jalon 2 sont des plafonds, pas des prévisions.
Et la demande 👤 la plus importante de `todo.md` — obtenir des classeurs Tableau et
quelques questions métier réelles — reste la priorité sociale du projet, à lancer
en parallèle du jalon 0.
