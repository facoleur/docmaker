# POC — couche sémantique automatisée, domaine Qualité de service

Plan d'implémentation. Contexte dans `couche-semantique.md`.
`plan-implementation.md` est périmé (mauvaise échelle) — supprimable.

## Cadre

|           |                                                                      |
| --------- | -------------------------------------------------------------------- |
| Périmètre | Domaine Qualité de service, datamart(s) rafraîchi(s) quotidiennement |
| Échelle   | **Centaines de tables**, milliers de colonnes, schéma en flocon      |
| Moyens    | Seul, quelques semaines                                              |
| Modèle    | ≤ 30B on-prem, runtime compris                                       |
| Public    | Managers Data Intel + DBA du projet QoS                              |
| Base      | Oracle, accès lecture acquis                                         |

**Contraintes fermes :**

- **la création de la métadonnée et du modèle sémantique est automatisée** — à cette
  échelle rien ne s'écrit à la main ; l'humain valide, il ne rédige pas ;
- **le modèle du runtime n'est pas bridé** — il peut répondre en texte, générer du
  SQL, corriger du SQL. On mesure ce qu'il fait avant de décider ce qu'on lui interdit ;
- **OpenMetadata est le store et l'UI de revue** de la métadonnée descriptive.
  Pas du modèle compilable : pas de typage, pas de mesures, pas de compilation.

**Posture :** le périmètre d'utilité du projet est défini par ce que le POC mesure,
pas l'inverse. On ne présuppose ni les questions, ni les utilisateurs, ni le régime
d'exécution. On construit le socle, on l'instrumente, on lit les chiffres.

**Écarté :** Cube (brique d'infra à défendre devant des DBA pour un gain que rien ne
mesure encore), base vectorielle (à réévaluer sur mesure de contexte, pas par
principe), le banc Oracle simulé de `openmetadata/database/`.

---

## Le problème d'évaluation, et sa solution

Sans questions métier connues, rien ne dit si le système progresse. Trois mesures
disponibles **aujourd'hui**, sans solliciter personne.

### 1. Masquage des commentaires existants

Les colonnes qui portent déjà un `COMMENT ON` sont un jeu de test gratuit : masquer
le commentaire, faire annoter par la chaîne, comparer à l'original.

Donne un **taux de justesse de l'annotation mesuré sur du réel**. C'est la mesure la
plus importante du POC, et elle ne coûte qu'une requête.

Si le taux de commentaires est nul, cette mesure disparaît — à vérifier dès le recon.

### 2. Masquage des FK déclarées

Même principe : retirer les FK connues de l'entrée, vérifier que le test d'inclusion
et le lineage les retrouvent. Note la détection de jointures, qui est le poste n°1
d'erreur en flocon.

### 3. Auto-cohérence

Réannoter une colonne à partir d'un sous-ensemble différent de preuves. Une
description qui change du tout au tout signale une inférence fragile — à router vers
la revue humaine en priorité.

**Ces trois mesures pilotent le POC tant qu'il n'y a pas de questions réelles.** Dès
que des rapports existants sont obtenus (§ Étape 7), elles passent au second plan.

---

## Étape 1 — Recon et catalogue

`docmaker/pipeline/recon.py` existe et n'a jamais tourné. Le pointer sur le vrai
périmètre (`owners` dans `config.toml`), lire le rapport, figer les schémas.

Puis `catalog` : colonnes, types, `NOT NULL`, PK, FK, index, partitions, synonymes,
`ALL_TAB_COMMENTS` / `ALL_COL_COMMENTS`, `ALL_VIEWS.TEXT`, `ALL_MVIEWS.QUERY`,
`ALL_SOURCE`, `ALL_DEPENDENCIES`. Une requête par vue catalogue, pas une par table.
Sérialisation triée (FQN, puis position).

**Sortie** `build/recon.json`, `build/catalog.json`
**Critères** deux exécutions bit-identiques · volumes réels chiffrés (tables,
colonnes, vues, MV, packages) · **taux de `COMMENT ON`** (décide si la mesure 1
existe) · **taux de parsing `sqlglot`** (décide si le lineage est viable) · nombre de
FK déclarées (décide de la taille du jeu de la mesure 2).

---

## Étape 2 — Priorisation · obligatoire à cette échelle

Sans logs de requêtes ni questions connues, il faut un classement de centralité,
sinon l'annotation LLM part sur des centaines de tables indifféremment.

| Signal                 | Source                      | Ce qu'il approche                                                                    |
| ---------------------- | --------------------------- | ------------------------------------------------------------------------------------ |
| Exposition             | `ALL_TAB_PRIVS`             | objets donnés en lecture aux rôles de restitution → **le meilleur proxy disponible** |
| Activité de chargement | `ALL_TAB_MODIFICATIONS`     | ce qui vit vs. ce qui est figé — très discriminant sur un datamart quotidien         |
| Fan-out                | `ALL_DEPENDENCIES`          | centralité structurelle                                                              |
| Volume                 | `NUM_ROWS`                  | faits vs. reliquats                                                                  |
| Fraîcheur              | `LAST_ANALYZED`, partitions | ce qui est encore alimenté                                                           |

**Sortie** `build/priority.json` — un score par table, chaque signal conservé
séparément (un score agrégé opaque n'est pas défendable devant un DBA).
**Critère** le top 50 est plausible à la lecture ; les tables techniques et le
staging sont en bas.

Demande à formuler en parallèle : `SELECT_CATALOG_ROLE` pour `V$SQL` — le cache de
curseurs seul, sans AWR ni licence Diagnostic Pack. Demande étroite, peu coûteuse,
et c'est le seul signal d'usage réel atteignable.

---

## Étape 3 — Le gisement de sens : lineage colonne à colonne

À cette échelle, c'est la source qui porte l'essentiel du sens. `DIM_X.FLG_Y` ne veut
rien dire ; l'expression qui l'alimente le dit.

1. `ALL_DEPENDENCIES` d'abord — graphe objet à objet, gratuit, exact, sans parseur.
2. `sqlglot` (dialecte `oracle`) ensuite pour descendre au niveau colonne :
   expression de calcul, colonnes amont, objet source.
3. Sous-produits du même parsing : **graphe de jointures pondéré par fréquence**
   (donc chemin canonique identifiable), **colonnes mortes** (au DDL, alimentées par
   rien).

**Sortie** `build/lineage.json`
**Critères** pour 10 colonnes dérivées tirées au sort, l'expression remontée
correspond au texte de la vue, vérifié à la main · **la part non parsée est
explicitement marquée** — jamais de couverture implicitement complète.

---

## Étape 4 — Mesures déterministes

| Passe               | Méthode                                                                                                                                       | Alimente                       |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| Domaines de valeurs | `ALL_TAB_COL_STATISTICS` + `ALL_TAB_HISTOGRAMS` d'abord ; scan `GROUP BY` ciblé seulement si stats absentes/périmées **et** table prioritaire | dictionnaires code → libellé   |
| Maille              | `COUNT(*) = COUNT(DISTINCT clé)`                                                                                                              | `grain`, faux-amis de nommage  |
| Jointures           | inclusion `A.X ⊆ B.X` + unicité de chaque côté                                                                                                | arêtes **et cardinalité**      |
| Dernier lot         | colonne de date de chargement → `MAX`                                                                                                         | filtre par défaut de fraîcheur |

Sur des centaines de tables, les passes 2 et 3 ne se lancent **que sur le périmètre
prioritaire** (étape 2) — le test d'inclusion est quadratique en nombre de couples
candidats.

La passe « dernier lot » est propre au rafraîchissement quotidien : « aujourd'hui »
n'est presque jamais le dernier jour disponible (lot en retard, en échec, jour non
ouvré). Un `SYSDATE` naïf rend un résultat vide ou faux sans lever d'erreur.

**Sortie** `build/profile.json`, `build/joins.json`
**Critère** chaque table du top 50 a sa clé, sa maille, et ≥ 1 chemin vers ses
dimensions avec son niveau de preuve (déclarée / observée dans le SQL / vérifiée par
inclusion).

---

## Étape 5 — Inférence sémantique automatique

Le cœur du POC. En flocon, une entité métier est éclatée sur plusieurs tables ; il
faut la reconstituer **sans l'écrire à la main et sans se fier aux préfixes de noms**.

Le signal est dans le graphe, pas dans le vocabulaire :

- deux tables **toujours jointes sur la même clé** dans le lineage appartiennent à la
  même entité ;
- une table jointe en **1:1 ou N:1 obligatoire** à une autre est son satellite, pas
  une entité ;
- les tables **alimentées par le même package / flux** forment un domaine ;
- une table dont la clé est unique et qui est référencée par des faits est une
  **dimension** ; une table au grain fin, volumineuse, portant des colonnes
  numériques additives est un **fait**.

Clustering du graphe de jointures pondéré → entités candidates. Le LLM **nomme**, il
ne construit pas — cohérent avec `couche-semantique.md` §3.

```yaml
# semantic/model.yaml — généré, puis amendé par la revue
entities:
  incident: # nom proposé par LLM, validé par un humain
    root: DMT.DMT_F_QOS_INC
    satellites: [DMT.DMT_F_QOS_INC_DET, REF.REF_D_QOS_TYP]
    grain: "un incident déclaré" # étape 4
    key: ID_INC
    confidence: 0.82
    evidence: [lineage:3_jointures, inclusion:verifiee, package:PKG_CHG_QOS]
    default_filters: # inférés, à valider
      - "FLG_ACT = 1"
      - "DT_CHG = (SELECT MAX(DT_CHG) FROM DMT.DMT_F_QOS_INC)"
    dimensions:
      statut: { expr: STA_INC, values: { OUV: Ouvert, RES: Résolu } }
    measures:
      nb_incidents: { expr: "COUNT(*)" }
```

**Sortie** `semantic/model.yaml` + `build/entities.json` (candidats avec preuves)
**Critères** chaque entité porte sa preuve et sa confiance · les entités de confiance
basse sont routées en revue prioritaire · le nombre d'entités est très inférieur au
nombre de tables (sinon le clustering n'a rien fait).

---

## Étape 6 — Annotation LLM et revue via OpenMetadata

L'annotation ne construit rien : elle nomme, ancrée sur des preuves déterministes.
Contexte par colonne : expression de lineage, valeurs observées, tokens décodés du
nom, `COMMENT ON` s'il existe.

**« Preuve insuffisante » doit rester une réponse acceptable** — un taux de refus nul
est un signal d'alarme, pas un succès.

Chaîne existante, à réutiliser telle quelle :

```
enrich/describe.py  → Proposal → sink/omd.py → OMD
                                  ├─ state=Suggested, tag « IA – à valider »
                                  ├─ lastSourceHash (idempotence)
                                  └─ jamais d'écrasement d'une correction humaine
```

**Ce qui manque et qu'il faut écrire : la boucle de retour.** `sink/omd.py` écrit,
rien ne relit — ce qu'un humain corrige dans l'interface n'existe pour personne
d'autre. `sources/omd_feedback.py` :

- description **confirmée** → `confidence = 1.0`, `human_touched = True` dans le pivot ;
- description **corrigée** → la correction devient la vérité, et l'écart
  proposition → correction est consigné. Ces écarts sont la seule trace de la
  connaissance tacite que le projet existe pour capturer.

La validation exhaustive est impossible à cette échelle : faire valider **le top-N
prioritaire et les inférences de basse confiance**, pas le reste.

**Sorties** métadonnée dans OMD · `build/feedback.json`
**Critères** `src/eval/coverage.py` mesure la progression (% tables et colonnes
décrites) — c'est le KPI présentable aux managers, indépendamment du text-to-SQL ·
une correction faite dans l'UI se retrouve dans le pivot au run suivant et n'est
jamais réécrasée.

---

## Étape 7 — Runtime, non bridé

Le modèle reçoit le contexte sémantique et fait ce que la question demande :
répondre, générer du SQL, ou **corriger du SQL fourni** (usage probablement le plus
rentable à court terme, et le plus facile à démontrer).

Pas de format de sortie imposé au POC. Ce qui est imposé, c'est de **savoir ce qu'il
a fait** : chaque réponse est étiquetée par régime — réponse libre / SQL généré / SQL
corrigé — et mesurée séparément. Sans cette distinction, le taux global est
ininterprétable.

**Budget de contexte** — problème réel à cette échelle. Hiérarchie à deux niveaux :

- niveau 1, toujours présent : **sommaire des entités** (nom, ce qu'elle décrit, ses
  mesures) — quelques milliers de tokens ;
- niveau 2, à la demande : **détail des entités retenues**, sélectionnées par le
  graphe (métrique citée → son entité + voisins à distance 1) ou par un appel léger
  sur le sommaire.

Mesurer les tokens du contexte assemblé et le taux de sélection correcte. Une base
vectorielle ne se justifie que si cette mesure montre que le routage échoue.

**Le validateur — contrainte non négociable, et il ne bride pas le modèle :**

1. parse `sqlglot` dialecte `oracle` — échec ⇒ rejet ;
2. **toute table et colonne citée existe dans `catalog.json`** — sinon rejet. Le plus
   gros rendement pour le plus petit code ;
3. toute jointure utilisée est une arête de `joins.json` — sinon avertissement ;
4. filtres par défaut des tables touchées présents — sinon avertissement ;
5. `EXPLAIN PLAN` — cardinalité estimée aberrante ⇒ rejet ;
6. exécution : compte en lecture seule, `FETCH FIRST n ROWS ONLY`, timeout.

Après _n_ échecs : **abstention motivée**, jamais une réponse plausible.

**Le jeu de questions, dès qu'il est obtenable.** Les rapports existants du domaine
sont la seule source traçable : un rapport produit chaque mois est une question que
quelqu'un a jugée digne d'être posée, et son SQL existe déjà. La question se
rétro-écrit en cinq minutes. Split train/test figé à la création, jamais modifié.
À demander maintenant — c'est le seul point à délai social.

---

## Étape 8 — La démo

Deux publics, presque deux critères opposés. Les deux imposent d'**afficher le SQL**.

**Managers Data Intel** — reproduire un chiffre qu'ils connaissent déjà par cœur.
Puis : montrer deux calculs plausibles d'un même indicateur QoS et la définition
figée qui tranche. Les indicateurs de qualité de service sont définitionnellement
disputés (jours ouvrés ou calendaires, délai depuis la déclaration ou la prise en
charge, incident rouvert compté une ou deux fois) — c'est là que la couche sémantique
se démontre le mieux.

Complément indépendant du text-to-SQL : le catalogue OMD peuplé, avec la courbe de
couverture. Livrable visible même si le runtime déçoit.

**DBA** — ce que le système **refuse** de faire. Lecture seule, rejet d'un objet hors
catalogue, `EXPLAIN` préalable, timeout, abstention sur une question hors périmètre.
Puis le piège de fraîcheur : `SYSDATE` sur un lot non chargé, attrapé par le filtre
par défaut. C'est le moment qui les convainc.

---

## Structure de code

Les deux arbres fusionnent : le POC utilise `src/` (Proposal, sink OMD, eval) **et**
`docmaker/` (config, recon, pipeline).

```
docmaker/
  config.py, models.py
  pipeline/
    recon.py                 existant, jamais exécuté
    catalog.py               étape 1
    priority.py              étape 2
    lineage.py               étape 3
    profile.py               étape 4 — valeurs, maille, dernier lot
    joins.py                 étape 4 — inclusion + cardinalité
  semantic/
    infer.py                 étape 5 — clustering du graphe → entités
    model.py                 chargement/validation de model.yaml
    prompt.py                étape 7 — sommaire + détail, budget de tokens
    validate.py              étape 7 — le validateur
  eval/
    masking.py               mesures 1 et 2 — masquage commentaires et FK
    runtime.py               étape 7 — par régime
src/                         conservé : proposal, enrich/describe, sink/omd, eval/coverage
  sources/omd_feedback.py    à écrire — la boucle de retour
semantic/model.yaml          généré étape 5, amendé par la revue
build/                       artefacts versionnés (diff = veille de dérive)
```

Dépendances déjà présentes : `oracledb`, `sqlglot`, `pydantic`, `openai`,
`openmetadata-ingestion`, `ollama`.

---

## Ordre

| #   | Étape                                          | Pourquoi là                                                       |
| --- | ---------------------------------------------- | ----------------------------------------------------------------- |
| 1   | 1 — recon + catalogue                          | Rien ne le bloque, et ses chiffres décident de tout le reste      |
| 2   | 2 — priorisation                               | Sans elle l'annotation part dans toutes les directions            |
| 3   | 3 — lineage                                    | Le gisement de sens ; conditionné au taux de parsing de l'étape 1 |
| 4   | 4 — mesures                                    | Sur le périmètre prioritaire seulement                            |
| 5   | 6 — annotation + OMD + **mesure par masquage** | Premier chiffre de qualité réel                                   |
| 6   | 5 — inférence sémantique                       | Demande le lineage et les jointures                               |
| 7   | 7 — runtime + validateur                       | Le validateur avant le reste : peu de code, gros rendement        |
| 8   | 8 — démo                                       |                                                                   |

Si le temps manque : **le catalogue, le lineage, l'annotation mesurée par masquage et
le catalogue OMD peuplé** constituent déjà un livrable défendable devant les deux
publics, sans runtime.

---

## Risques

| Risque                                     | Parade                                                                                                            |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| Aucun `COMMENT ON` dans la base            | La mesure 1 disparaît → se rabattre sur le masquage de FK et l'auto-cohérence. Mesuré à l'étape 1                 |
| Taux de parsing `sqlglot` faible           | Le lineage colonne s'effondre → replier sur `ALL_DEPENDENCIES` (niveau table) et le profiling. Mesuré à l'étape 1 |
| Test d'inclusion trop coûteux              | Le restreindre au top-N prioritaire ; échantillonner avant de tester exhaustivement                               |
| Le clustering produit une entité par table | Le signal de jointure est trop faible → se rabattre sur les conventions de nommage, en le marquant comme dégradé  |
| Budget de contexte dépassé                 | Mesuré à l'étape 7 ; c'est seulement là que la question du retrieval se pose                                      |
| Le 30B sature sur des centaines d'entités  | Réduire le sommaire aux entités prioritaires, mesurer la dégradation                                              |
| Les rapports existants n'arrivent jamais   | Le POC reste pilotable par les trois mesures d'auto-évaluation                                                    |
