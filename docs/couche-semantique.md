# De la doc à la couche sémantique : sources déterministes et accès

Date de création : 2026-09-10. **Document vivant** — il est mis à jour à mesure
que le besoin et les accès réels se précisent. Le §4 (inventaire des accès) et
le §7 (questions ouvertes) sont les sections à tenir à jour en priorité ; le
journal des révisions est en §9.

Complément à `prise-de-recul.md` : celui-ci pose la question de l'accès à la
base (§2) puis suppose que la réponse est **non**. Ce document part de
l'hypothèse inverse, désormais confirmée : **l'accès en lecture sur le datamart
est acquis, la base est Oracle.** Il en tire les conséquences.

Cadre arrêté au 2026-09-10 : **Oracle**, accès en lecture complet, **pas d'outil
d'ETL dédié** (« c'est surtout du SQL »), **pas de dépôt Git côté DBA**, et
**pas d'accès aux logs de requêtes** — cette dernière contrainte est prise comme
définitive et le document en tire les substituts.

---

## 1. Le changement de cible

Le livrable n'est plus une documentation Markdown. C'est une **couche
sémantique** dont la finalité est qu'un LLM traduise une question en langage
naturel en SQL correct sur un datamart analytique.

Ce déplacement n'est pas cosmétique : un text-to-SQL n'a pas prioritairement
besoin de prose descriptive. Il a besoin, par ordre d'importance décroissante :

1. **Le graphe de jointures**, avec le chemin canonique quand plusieurs
   existent. En schéma en flocon, le mauvais chemin de jointure est la première
   cause de réponse fausse — devant l'incompréhension de la question.
2. **Les dictionnaires de valeurs** (code → libellé), sans quoi « les clients
   actifs » ne devient jamais `CLI_STAT_CD = 'A'`.
3. **La définition des métriques** : quelle colonne, quel filtre, quelle
   **maille**. Le fan-out d'une jointure sur une dimension multi-valuée est
   l'erreur silencieuse classique en analytics.
4. **Un jeu d'exemples question → SQL validés.** Quelques dizaines de paires
   battent systématiquement des pages de description.
5. La prose descriptive — utile, mais dernière.

C'est plus proche d'un semantic model dbt ou d'un Cube que d'une documentation.
OpenMetadata peut en héberger une partie (tables, colonnes, descriptions,
lineage, glossaire — il ingère nativement du lineage SQL) mais les métriques,
les chemins canoniques et les exemples few-shot resteront hors catalogue.

### La difficulté propre à ce datamart

Beaucoup de dimensions, beaucoup de flux, et des noms de tables, colonnes, vues
et flux **très obscurs**. Le problème central n'est pas d'inventorier la
structure : c'est de **retrouver la signification réelle** derrière les noms,
pour la rendre en texte compréhensible par le LLM final.

---

## 2. Les sources déterministes, par valeur décroissante

Principe directeur : **tout ce qui peut être obtenu mécaniquement doit l'être.**
Le LLM n'intervient qu'après, sur ce qui reste, et toujours ancré sur des
preuves produites par ces sources.

### 2.1 — Le catalogue / DDL : le squelette vrai

Tables, vues, colonnes, types, `NOT NULL`, PK, index. Gratuit, exhaustif, zéro
hallucination. Sur Oracle on lit directement les vues catalogue (§5.1) — inutile
de générer puis de reparser du texte DDL.

Ça règle d'un coup les paris fragiles de `prise-de-recul.md` : le **vocabulaire
fermé** est donné, plus de rapprochement de `ACCOUNT` / `T_ACCOUNT` / « la table
des comptes » par normalisation de chaîne.

**Piège spécifique aux datamarts : les FK ne sont presque jamais déclarées.**
Chargement par ELT, contraintes désactivées pour la performance. Le DDL donne
donc le vocabulaire mais **pas le graphe de jointures** — c'est-à-dire pas ce
dont le text-to-SQL a le plus besoin. Voir 2.2 et 2.4 pour le récupérer.

### 2.2 — Le lineage colonne à colonne des vues et de l'ETL : le gisement de sens

**C'est la source la plus précieuse, et celle qui répond au problème des noms
obscurs.** Dans un datamart, le sens d'une colonne est encodé dans son calcul.

`DIM_CLI_T7.FLG_X` ne veut rien dire. Mais si la vue qui l'alimente contient :

```sql
CASE WHEN CLI_STAT_CD IN ('A','P') THEN 1 ELSE 0 END AS FLG_X
```

et que `CLI_STAT_CD` remonte à une table source documentée « statut client :
A=actif, P=prospect », alors `FLG_X` = « client actif ou prospect ».
**Aucun document ne contient cette information** ; elle est déductible
exactement du SQL. C'est vrai de la majorité des colonnes dérivées d'un
datamart : agrégats, flags, bucketing, montants retraités.

Outil : `sqlglot.lineage.lineage(column, sql, schema=...)` remonte l'arbre des
dépendances d'une colonne à travers vues et CTE jusqu'aux colonnes physiques, et
rend l'expression de calcul à chaque niveau.

Sous-produits gratuits du même parsing :

- **Le graphe de jointures réel**, extrait des clauses `JOIN`, pondéré par la
  fréquence d'apparition — donc avec un chemin canonique identifiable.
- **Les colonnes mortes** : présentes au DDL, alimentées par rien, lues par
  personne.
- **Le graphe de flux** entre tables, indépendamment de tout outil d'ETL.

Morceau non trivial du chantier : dialecte à caler, procédures stockées, SQL
généré dynamiquement (concaténation de chaînes) qui échappe au parseur. Mais
c'est du travail borné et rejouable, pas de l'inférence.

### 2.3 — Le profiling de valeurs

Une colonne `TYP_MVT` avec six valeurs distinctes
`('VIR','PRL','CHQ','CB','ESP','AUT')` est quasi auto-documentée pour un LLM.

Par colonne : nombre de valeurs distinctes, top-N valeurs avec fréquence, taux
de null, min/max, cardinalité. On passe de « nom opaque » à « nom opaque +
domaine réel ». Double usage : indice de sens pour le nommage (§3), et matière
première du **dictionnaire de valeurs** dont le text-to-SQL a besoin (§1.2).

**Sur Oracle, une grande partie est déjà calculée et gratuite.** Les
statistiques de l'optimiseur contiennent l'essentiel, sans le moindre scan :

| Vue | Ce qu'elle donne |
| --- | --- |
| `ALL_TAB_COL_STATISTICS` | `NUM_DISTINCT`, `NUM_NULLS`, `LOW_VALUE`, `HIGH_VALUE`, `DENSITY`, `HISTOGRAM`, `LAST_ANALYZED` |
| `ALL_TAB_HISTOGRAMS` | **les valeurs distinctes elles-mêmes**, pour les histogrammes de fréquence |
| `ALL_TAB_STATISTICS` | `NUM_ROWS`, `AVG_ROW_LEN`, `LAST_ANALYZED` par table et par partition |

Le point remarquable : un histogramme de fréquence n'est construit que sur les
colonnes de **faible cardinalité** — c'est-à-dire exactement les colonnes de
code dont on veut le dictionnaire de valeurs. Quand il existe, `ALL_TAB_HISTOGRAMS`
livre le domaine complet sans lire une seule ligne de données.

Réserves, toutes vérifiables par requête :

- `LOW_VALUE` / `HIGH_VALUE` sont des `RAW` à décoder
  (`DBMS_STATS.CONVERT_RAW_VALUE`).
- Les statistiques peuvent être **périmées** : contrôler `LAST_ANALYZED`.
- Un histogramme n'existe que s'il a été demandé (`METHOD_OPT`) ; son absence ne
  dit rien sur la colonne.
- Les valeurs des histogrammes sont tronquées à 32 octets pour les chaînes.

**Stratégie qui en découle :** première passe gratuite sur les statistiques,
puis scans `GROUP BY` ciblés uniquement là où les stats manquent, sont périmées,
ou concernent une colonne du périmètre prioritaire. Ça transforme le profiling
d'un chantier lourd en une lecture de catalogue plus quelques requêtes.

### 2.4 — Le test d'inclusion : retrouver les FK non déclarées

Pour chaque couple de colonnes candidat (même nom, ou nom proche, ou type et
cardinalité compatibles) : `FACT.CLI_ID ⊆ DIM_CLI.CLI_ID` ?

Une requête confirme ou infirme une FK présumée, mécaniquement. Combiné au
graphe de jointures de 2.2, ça donne un graphe à la fois **observé** (ce que
l'ETL fait) et **vérifié** (ce que les données permettent).

### 2.5 — La priorisation, sans les logs de requêtes

Les logs de requêtes seraient le signal le plus fort — quelles tables sont
réellement utilisées, quels chemins de jointure sont réels, quelles colonnes
sont mortes. **On part du principe qu'ils sont hors d'atteinte** (voir §5.4).

Le besoin qu'ils servaient reste entier, et c'est le plus important de tous : sur
un datamart de plusieurs centaines de tables, ~80 % des questions tapent ~15 %
du modèle. Documenter le reste au même niveau de soin est du gaspillage. Il faut
donc un **classement de centralité** obtenu autrement. Cinq substituts, tous
disponibles avec l'accès actuel sauf le dernier :

| Substitut | Vue / source | Ce qu'il approche |
| --- | --- | --- |
| Fan-out des dépendances | `ALL_DEPENDENCIES` | Combien d'objets dépendent de cette table → centralité structurelle |
| Activité d'alimentation | `ALL_TAB_MODIFICATIONS` | Ce qui est réellement chargé vs. ce qui est figé |
| Volume | `ALL_TAB_STATISTICS.NUM_ROWS` | Les tables de fait vs. les reliquats |
| Fraîcheur | `LAST_ANALYZED`, `ALL_TAB_PARTITIONS` | Ce qui vit encore |
| Exposition | `ALL_TAB_PRIVS` | Ce qui est donné en lecture aux rôles de restitution → **objets destinés aux consommateurs** |

Le dernier est sous-estimé : dans un datamart, l'ensemble des objets sur
lesquels les rôles de reporting ont un `SELECT` est une excellente approximation
du périmètre qui compte.

**Deux sources humaines valent mieux que tous ces substituts réunis**, et elles
sont à demander explicitement :

- **La couche sémantique d'un outil de BI**, s'il en existe un (Business
  Objects, Cognos, Tableau, Power BI). Un univers BO contient des **libellés
  humains déjà mappés sur des colonnes physiques** — c'est-à-dire, littéralement,
  le livrable de ce projet, partiellement déjà construit. À vérifier en priorité.
- **La liste des 20 tables que les analystes utilisent vraiment.** Dix minutes
  de conversation remplacent des semaines d'inférence.

Note : si `SELECT_CATALOG_ROLE` devenait accessible, `V$SQL` seul — le cache de
curseurs, sans AWR ni licence Diagnostic Pack — fournirait déjà un échantillon
utile à coût nul. C'est une demande étroite et peu coûteuse à formuler, distincte
d'un « accès aux logs ».

### 2.6 — Le dictionnaire d'abréviations : meilleur ratio effort/gain

Les noms obscurs d'un datamart bancaire sont rarement aléatoires : ils sont
**systématiquement abrégés**. `DT_` date, `MT_` montant, `CD_` code, `LIB_`
libellé, `FLG_` flag, `NB_` nombre, `_T7` une variante de table…

Méthode : découper tous les noms sur `_` (et sur les frontières de casse),
compter les tokens par fréquence, faire **valider les ~150 plus fréquents par un
humain en une heure**. On décode ensuite mécaniquement des milliers de colonnes.

C'est le vocabulaire fermé de `prise-de-recul.md` §3.1 appliqué aux **tokens**
plutôt qu'aux noms de tables. Coût dérisoire, portée maximale.

### 2.7 — Les documents Office : rôle réduit et confortable

Ils ne portent plus le schéma, seulement le **sens métier** : règles, glossaire,
historique, pièges, décisions. Conséquences :

- La difficulté n'a jamais été la conversion (docling la fait) mais la
  contradiction et la péremption. Avec le catalogue comme colonne vertébrale,
  une contradiction sur un type n'est plus un conflit à arbitrer : **c'est un
  document qui a tort.**
- `reconcile.py` et `conflicts.py` maigrissent énormément.
- L'extraction LLM passe de « inventer des tables » à « rattacher une phrase à
  une table connue » — tâche fermée, vérifiable, rejetable.

---

### 2.8 — La couche sémantique Tableau : trois problèmes résolus d'un coup

Placée en fin de §2 pour ne pas renuméroter, mais **par valeur elle se situe
juste après le lineage (§2.2)**, voire devant si le parc Tableau est fourni.

Un classeur Tableau (`.twb`, ou `.twbx` décompressé) est du XML. Il contient :

| Élément | Ce qu'il vaut ici |
| --- | --- |
| `caption` / alias de champ | **Un libellé humain déjà mappé sur une colonne physique** — le livrable du projet, partiellement pré-construit par quelqu'un d'autre |
| Champs calculés | La **définition des métriques** (§1.3), écrite par ceux qui les utilisent |
| Datasource : SQL personnalisé, ou tables + jointures | Des **chemins de jointure validés par l'usage réel** (§1.1) |
| Membres de filtres | Des **dictionnaires de valeurs** (§1.2), déjà décodés côté métier |
| Titres de feuilles et de tableaux de bord | Des **questions métier formulées en langage humain** |
| Hiérarchies | La structure dimensionnelle telle qu'elle est comprise |

Trois besoins distincts du projet sont couverts par la même source :

1. **La priorisation** — les tables et colonnes qui alimentent les tableaux de
   bord les plus consultés. C'est le **substitut le plus proche des logs de
   requêtes** écartés au §5.4.
2. **Les libellés humains** — le cœur du livrable, déjà écrit pour la part la
   plus utilisée du datamart.
3. **Le jeu d'évaluation** — un titre de feuille est une question, la requête
   sous-jacente est sa réponse. Voir §7 bis.

**Deux voies d'accès, très inégales :**

- **Les fichiers** (`.twb` / `.twbx`) — suffit d'en obtenir une copie, aucun
  droit particulier, parsing XML immédiat. Voie la plus simple.
- **L'API Metadata de Tableau Server / Cloud** (GraphQL) — expose le lineage
  **jusqu'au niveau colonne** sur l'ensemble des classeurs et sources publiés,
  plus les statistiques de consultation via les vues d'administration. Voie la
  plus riche ; demande un accès et un token.

Réserve : Tableau ne couvre que la part du datamart effectivement restituée. Il
ne dit rien du socle et des zones de préparation — mais il dit précisément
**quelle part compte pour les utilisateurs**, ce qu'aucune autre source
disponible ne dit.

---

## 3. Ce qui reste au LLM

Une fois le socle posé, **le LLM ne construit plus rien : il nomme.**

Prompt type, entièrement ancré sur des preuves déterministes :

> Voici la colonne `FLG_X` : son expression de calcul (lineage), ses valeurs
> observées (profiling), les tokens décodés de son nom (dictionnaire
> d'abréviations), et les trois passages de documentation qui mentionnent sa
> table. Propose un libellé humain et une description.

Tâche fermée, vérifiable, et **« preuve insuffisante » doit rester une réponse
acceptable** — préférable à une description plausible et fausse.

L'axe anti-hallucination de `prise-de-recul.md` est conservé intégralement : la
provenance est attachée par le code, jamais par le modèle ; le squelette est
rendu depuis le store, jamais généré ; tout ce qui n'est pas validé par un
humain est marqué comme tel.

**Ce qui reste hors de portée du déterministe et du LLM :** l'intention métier
qui n'est écrite nulle part et ne se déduit d'aucun calcul. Pour cette part, la
seule source est l'expert — et le rôle du reste du pipeline est de réduire sa
sollicitation à ces cas-là, pas de la supprimer.

---

## 4. Inventaire des accès

**Section à tenir à jour.** État au 2026-09-10 :

| Source | Statut | Détail |
| --- | --- | --- |
| **SGBD** | **Oracle** | Confirmé. Toutes les requêtes du §5 sont écrites pour ce dialecte. |
| Lecture sur tout le datamart | **acquis** | Confirmé. Débloque catalogue, texte des vues, statistiques, profiling, tests d'inclusion. |
| Catalogue / DDL | **acquis de fait** | `ALL_TAB_COLUMNS` & co., interrogeables avec le seul accès en lecture. |
| Texte des vues et vues matérialisées | **acquis de fait** | `ALL_VIEWS.TEXT`, `ALL_MVIEWS.QUERY`. Cœur du gisement de sens. |
| Source PL/SQL (procédures, packages) | **à vérifier** | `ALL_SOURCE` — visible pour les objets sur lesquels on a un privilège. |
| Jobs planifiés dans la base | **à vérifier** | `ALL_SCHEDULER_JOBS.JOB_ACTION` si l'ordonnancement est interne. |
| Commentaires Oracle (`COMMENT ON`) | **inconnu — fort enjeu** | `ALL_TAB_COMMENTS`, `ALL_COL_COMMENTS`. Mesurable en une requête (§5.1). |
| Statistiques de l'optimiseur | **inconnu** | Détermine si le profiling gratuit du §2.3 fonctionne. Mesurable en une requête. |
| Outil d'ETL dédié | **probablement inexistant** | « C'est surtout du SQL ». À confirmer — change tout si faux. |
| Dépôt Git côté DBA | **inexistant** (supposé) | Conséquence : pas d'historique, pas de scripts versionnés à lire. |
| Logs de requêtes | **indisponible** | Hypothèse de travail arrêtée. Substituts en §2.5. |
| **Connexion directe depuis le poste de dev** | **acquis** | Confirmé. Le code peut interroger la base directement (`python-oracledb`), pas besoin de la forme « script SQL + spool ». |
| **Classeurs / serveur Tableau** | **probable** | Applications BI sur Tableau confirmées, accès non encore obtenu. Enjeu majeur — voir §2.8. |
| Documents Office | acquis (principe) | Non disponibles dans l'environnement de développement actuel. |
| Périmètre (`OWNER` concernés) | **inconnu** | Bloquant pour cadrer les requêtes. |
| Volume (tables / colonnes / vues) | **inconnu** | Mesurable en une requête. Conditionne coût LLM et stratégie de profiling. |

## 5. Comment obtenir chaque source — Oracle

Tout ce qui suit se lit avec un simple privilège `SELECT`. Les vues `ALL_*`
montrent ce que le compte connecté a le droit de voir — c'est le bon préfixe
ici ; `DBA_*` (tout) est généralement refusé, `USER_*` (le seul schéma courant)
est trop étroit pour un datamart réparti sur plusieurs `OWNER`.

Connexion : **`python-oracledb` en mode thin** — pas d'Oracle Instant Client à
installer, pas de variable `LD_LIBRARY_PATH`, une dépendance Python et une
chaîne de connexion suffisent. C'est le chemin à privilégier.

### 5.1 — Le squelette : catalogue et commentaires

| Vue | Contenu | Remarque |
| --- | --- | --- |
| `ALL_OBJECTS` | Inventaire par type et par `OWNER` | La première requête à écrire : elle donne le volume et le périmètre |
| `ALL_TABLES` | Tables, `NUM_ROWS`, partitionnement | |
| `ALL_TAB_COLUMNS` | Colonnes, types, `NULLABLE`, `DATA_DEFAULT` | Couvre aussi les colonnes des vues |
| `ALL_CONSTRAINTS` + `ALL_CONS_COLUMNS` | PK, FK, `CHECK`, `NOT NULL` | **Peu de FK attendues** ; noter le `STATUS` et le `VALIDATED` |
| `ALL_INDEXES` + `ALL_IND_COLUMNS` | Index | Indice de chemin d'accès privilégié, donc de jointure fréquente |
| `ALL_TAB_COMMENTS` / `ALL_COL_COMMENTS` | **Commentaires métier natifs** | Voir ci-dessous |
| `ALL_SYNONYMS` | Synonymes | À résoudre avant tout rapprochement de noms |
| `ALL_TAB_PARTITIONS` | Partitions | Donne la maille temporelle des tables de fait |

**Les commentaires méritent la toute première requête du projet.** Oracle stocke
nativement une description par table et par colonne (`COMMENT ON`). Si le
datamart en est doté, ne serait-ce que partiellement, c'est de la sémantique
humaine déjà écrite, gratuite et fiable — et la taille du chantier change
d'ordre de grandeur. S'il n'y en a aucun, on le sait en dix secondes et on
arrête de l'espérer.

Sur `DBMS_METADATA.GET_DDL()` : pratique mais souvent bloqué hors de son propre
schéma. Ne pas en dépendre — le squelette se reconstruit intégralement depuis
`ALL_TAB_COLUMNS` et `ALL_CONSTRAINTS`.

### 5.2 — Le sens : où vit la transformation quand il n'y a pas d'outil d'ETL

C'est la conséquence la plus importante du cadre « pas d'outil d'ETL, pas de
Git, surtout du SQL » : **la logique de transformation est très probablement
dans la base**, donc déjà accessible.

| Où | Vue Oracle | Remarque |
| --- | --- | --- |
| Vues | `ALL_VIEWS.TEXT` | Colonne de type `LONG` — `python-oracledb` la rend en `str` sans difficulté. `TEXT_VC` existe en 18c+ mais tronque à 4000 caractères |
| Vues matérialisées | `ALL_MVIEWS.QUERY` | **Très fréquentes dans un datamart Oracle** — souvent là que vivent les agrégats |
| Procédures, fonctions, packages, triggers | `ALL_SOURCE` | Texte ligne par ligne, à recoller par `NAME` / `TYPE` / `LINE` |
| Dépendances objet à objet | `ALL_DEPENDENCIES` | **Lineage au niveau table, gratuit et exact, sans parsing** |
| Jobs planifiés dans la base | `ALL_SCHEDULER_JOBS.JOB_ACTION` | Si l'ordonnancement est interne à Oracle |

`ALL_DEPENDENCIES` est à exploiter avant même le parsing : Oracle maintient
lui-même le graphe « quel objet dépend de quel objet ». C'est le graphe de flux
du §2.2 au niveau table, obtenu sans écrire une ligne de parseur. Le parsing
`sqlglot` (dialecte `oracle`) sert alors à descendre au **niveau colonne**,
qui est là où se trouve le sens.

### 5.3 — Ce qui échappera au catalogue

À identifier tôt, parce que c'est l'angle mort du dispositif :

- Les scripts SQL lancés à la main ou par un ordonnanceur externe (Control-M,
  cron) depuis un serveur de traitement — invisibles depuis la base.
- Le SQL construit dynamiquement par concaténation dans du PL/SQL
  (`EXECUTE IMMEDIATE`) — présent dans `ALL_SOURCE` mais non parsable.
- Les chargements par `SQL*Loader` ou fichiers plats — la logique est dans le
  fichier de contrôle, hors base.
- Sans Git, **aucun historique** : on voit l'état courant, jamais l'intention ni
  l'évolution.

L'enjeu n'est pas de tout couvrir, mais de **savoir ce qui n'est pas couvert**
et de le marquer comme tel, plutôt que de laisser croire à une exhaustivité
fausse.

### 5.4 — Les logs de requêtes : écartés

Sous Oracle, `V$SQL` demande `SELECT_CATALOG_ROLE` et l'historique AWR
(`DBA_HIST_SQLTEXT`) demande en plus la licence **Diagnostic Pack** — un
véritable enjeu contractuel, pas une formalité. **On considère cette source
comme indisponible** et on s'appuie sur les substituts du §2.5.

## 6. Conséquences sur le pipeline actuel

| Composant | Devenir |
| --- | --- |
| `ingest` | Conservé, périmètre réduit aux documents Office. |
| `chunk` | Conservé. |
| `extract` | Conservé mais **contraint** : rattachement à un vocabulaire fermé issu du catalogue, plus de génération de noms de tables. |
| `reconcile` / `conflicts` | Fortement réduits : le catalogue tranche, les documents annotent. |
| `render` | Devient multi-backend depuis `model.json` : Markdown, `chunks.jsonl`, payload OpenMetadata, **modèle sémantique Cube** (§8.6). |
| **`catalog`** (nouveau) | Lecture du catalogue → squelette factuel. |
| **`lineage`** (nouveau) | Parsing des vues / procédures via `sqlglot` → expressions, graphe de jointures, graphe de flux. |
| **`profile`** (nouveau) | Profiling de valeurs + tests d'inclusion. |
| **`glossary`** (nouveau) | Dictionnaire d'abréviations miné puis validé. |
| **`annotate`** (nouveau) | Appel LLM de nommage, ancré sur les preuves des étapes ci-dessus. |

Dépendance à ajouter : `sqlglot`. Le pivot reste `model.json`, source unique
dont tout le reste dérive.

**Ordre de travail suggéré** — chaque étape a de la valeur seule, et les
premières ne dépendent d'aucun accès supplémentaire :

1. `catalog` — accès déjà acquis, valeur immédiate, zéro risque.
2. `glossary` (dictionnaire d'abréviations) — coût dérisoire, portée maximale.
3. `lineage` sur les vues — accès déjà acquis, c'est le gisement de sens.
4. `profile` — accès déjà acquis, à cadencer sur le volume.
5. Priorisation par les substituts du §2.5 — `ALL_DEPENDENCIES`,
   `ALL_TAB_PRIVS`, et surtout la couche sémantique BI si elle existe.
6. `annotate` (LLM) — en dernier, quand les preuves sont là.

Avant tout cela : une **passe de reconnaissance** en lecture seule qui mesure le
terrain (volume, périmètre, présence de commentaires, fraîcheur des
statistiques, proportion de transformation in-database). Sans ces chiffres,
aucune des étapes ci-dessus ne peut être dimensionnée.

---

## 7. Questions ouvertes

À résoudre au fil des échanges ; les réponses mettent à jour les §4 et §5.

### Mesurables par requête — la passe de reconnaissance y répond

1. **Quels `OWNER` composent le datamart ?** Staging, socle, marts, restitution
   sont souvent des schémas distincts. Bloquant pour cadrer toutes les requêtes.
2. **Quel volume ?** Nombre de tables, colonnes, vues, vues matérialisées,
   packages. Conditionne le coût LLM et la stratégie de profiling.
3. **Des `COMMENT ON` existent-ils déjà, et sur quelle proportion des colonnes ?**
   Enjeu majeur : change l'ordre de grandeur du chantier.
4. **Les statistiques sont-elles fraîches, des histogrammes existent-ils ?**
   Détermine si le profiling gratuit du §2.3 fonctionne.
5. **Quelle part de la transformation est in-database ?** Rapport entre le
   nombre de tables et le nombre de vues / MV / packages qui les alimentent.
   Une table de fait qu'aucun objet de la base n'alimente est chargée depuis
   l'extérieur — c'est la mesure de l'angle mort du §5.3.

### Ne se répondent que par une conversation

6. ~~Y a-t-il un outil de BI avec une couche sémantique ?~~ **Répondu :
   Tableau.** Reste à obtenir l'accès, et à trancher entre les fichiers `.twb`
   et l'API Metadata du serveur. Voir §2.8.
7. **Existe-t-il des questions métier réelles avec leur SQL connu ?**
   ⚠ **Le manque le plus important du projet.** Sans un jeu de 20 à 30 paires
   question → SQL validées, il n'y a ni évaluation, ni priorisation fondée, ni
   critère d'arrêt. Voir §7 bis.
8. **Comment sont lancés les traitements qui ne sont ni vue, ni MV, ni
   procédure ?** Ordonnanceur externe, scripts sur un serveur, chargements
   fichiers ?
9. **Qui valide, et combien de temps ?** Le dictionnaire d'abréviations et le
   périmètre prioritaire demandent une validation humaine courte mais
   indispensable.
10. **OpenMetadata est-il déployé et alimenté ?** Par quel connecteur ?
11. **Quel modèle assure le text-to-SQL final ?** La contrainte ≤ 30B on-prem
    s'applique-t-elle à lui, ou seulement au pipeline de construction ?
12. ~~D'où tourne le code ?~~ **Répondu : connexion directe possible depuis le
    poste de développement.** Reste à réunir service name / TNS et compte
    technique le jour J.
13. **Le LLM final émet-il du SQL, ou consomme-t-il une API sémantique ?**
    Décision d'architecture, liée à la 11 : elle détermine si les erreurs de
    jointure et de maille restent dans son champ ou en sortent. Voir §8.

### 7 bis — Le critère d'acceptation manquant

Rien dans le dispositif actuel ne dit **quand c'est bon**. C'est le trou
méthodologique le plus sérieux, et il précède les questions d'architecture.

Le remède est peu coûteux : obtenir de 20 à 30 **questions métier réelles**
formulées comme un utilisateur les poserait, avec le SQL correct correspondant,
écrit ou validé par quelqu'un qui connaît le datamart. Ce jeu sert quatre fois :

- **Critère d'évaluation** : on mesure le taux de SQL correct, avant / après
  chaque amélioration de la couche sémantique.
- **Priorisation** : les tables et colonnes touchées par ces questions sont, par
  construction, celles qui comptent.
- **Exemples few-shot** : c'est directement le §1.4.
- **Révélateur de manques** : chaque question qui échoue désigne précisément la
  connaissance absente.

Sans ce jeu, on optimise à l'aveugle un livrable dont personne ne peut dire s'il
progresse.

---

## 8. Le format cible : quelle couche sémantique, et qui écrit le SQL

Le §1 dit ce que la couche doit contenir, le §2 comment l'obtenir, le §6 qui le
produit. Reste la **forme du livrable** — et derrière elle une décision
d'architecture jamais tranchée : le LLM final **émet-il du SQL**, ou émet-il une
requête structurée que **quelque chose d'autre compile** en SQL ?

Trois familles d'outils se cachent derrière « semantic layer ». Les comparer à
plat est le premier piège.

### 8.1 — Les catalogues de métadonnées : un magasin de sens

OpenMetadata, DataHub, Atlan.

Tables, colonnes, descriptions, lineage, glossaire, profiling, ownership.
Interrogeable par un humain, **pas par un moteur** : aucune notion de métrique,
de maille, ni de chemin de jointure canonique. Un catalogue ne garantit aucun
SQL.

Ici c'est le réceptacle naturel du lineage du §2.2 et des descriptions du §3 —
déjà prévu comme backend de `render` au §6 — mais **ce n'est pas la couche
sémantique du §1**.

### 8.2 — Les semantic layers : le modèle est compilé en SQL

Une jointure déclarée avec sa cardinalité, une métrique déclarée avec sa maille :
le moteur choisit le chemin et gère le fan-out. Le client — BI ou LLM — demande
« dimensions + mesures + filtres », jamais du SQL.

| Outil | On-prem | Ce qu'il garantit | Limite ici |
| --- | --- | --- | --- |
| **Cube** (Core, Apache 2.0) | oui, complet | joins déclarés **avec cardinalité**, métriques et maille, pre-aggregations ; API SQL / REST / GraphQL / **MCP** | modèle à écrire en YAML ; les fonctions AI et de gouvernance sont réservées à Cube Cloud |
| **dbt / MetricFlow** | partiel | métriques *grain-aware*, entités, joins dérivés du modèle | MetricFlow est Apache 2.0 depuis fin 2025, mais **l'exposition requêtable reste dbt Cloud** ; suppose un projet dbt bâti sur le datamart — inexistant ici (§4 : pas d'ETL, pas de Git) |
| **LookML**, **AtScale** | non | fan-out résolu proprement (*symmetric aggregates*) | propriétaire et licencié, hors périmètre |
| **Snowflake Semantic Views**, **Databricks Metric Views** | non | équivalent, natif au warehouse | hors jeu : le datamart est Oracle on-prem |
| **Malloy** | oui | joins typés par cardinalité, syntaxe très lisible par un LLM | adoption et écosystème faibles — pari risqué pour une cible de production |

À noter : la **couche sémantique Tableau** du §2.8 appartient déjà de fait à
cette famille — champs renommés, calculs, relations entre sources. Elle n'est pas
requêtable comme un moteur générique, mais elle est le meilleur point de départ
pour peupler celui qu'on retiendra.

### 8.3 — Les agents text-to-SQL à couche de modélisation

WrenAI (self-host, modèle local via Ollama, modélisation en MDL), Vanna, DB-GPT.

Ce sont les plus proches de la finalité *bout en bout* : ils vont de la question
au résultat. Mais ils **produisent du SQL au lieu de le compiler** — le chemin de
jointure reste une inférence du modèle, pas une garantie du moteur. C'est
exactement le risque n°1 du §1. Utiles comme référence d'architecture et comme
banc d'essai ; pas comme garantie.

### 8.4 — Confrontation aux cinq besoins du §1

| Besoin du §1 | Couvert par un outil du marché ? |
| --- | --- |
| 1. Graphe de jointures et chemin canonique | **oui** — Cube, dbt, Malloy, LookML : c'est leur cœur |
| 2. Dictionnaires de valeurs (code → libellé) | **non** — aucun ne les produit ; certains savent les héberger |
| 3. Définition des métriques et de leur maille | **oui** — garanti par construction |
| 4. Exemples question → SQL validés | **non** — hors périmètre de tous |
| 5. Prose descriptive | partiellement — c'est le terrain du catalogue (§8.1) |

Les besoins 2 et 4, plus la **désobfuscation des noms par le lineage** (§2.2),
sont donc hors marché. Or ce sont les postes les plus coûteux du chantier, et la
valeur propre de docmaker. Conclusion : **aucun outil ne remplace le pipeline ;
le bon outil en est le consommateur.**

### 8.5 — Le levier décisif, sous contrainte ≤ 30B

Adopter un semantic layer n'est pas un gain de confort : cela **retire des
classes d'erreur au LLM**. Le mauvais chemin de jointure et le fan-out — les deux
premières causes de réponse fausse *silencieuse* — sortent de sa responsabilité
parce qu'il ne les exprime plus. Ce qui lui reste est le mapping question →
(dimensions, mesures, filtres) : une tâche fermée et vérifiable, du même type que
celle du §3.

Plus le modèle est petit, plus ce transfert vaut cher. Sous contrainte ≤ 30B
on-prem, c'est vraisemblablement le gain le plus important disponible — devant
toute amélioration de la prose ou du volume de contexte.

Contrepartie à ne pas masquer : **un moteur ne répond qu'aux questions que son
modèle prévoit.** Les questions exploratoires hors modèle deviennent impossibles
au lieu d'être fausses. Un dispositif à deux régimes — API sémantique pour ce qui
est modélisé, SQL généré et relu pour le reste — est plus réaliste qu'un choix
exclusif.

### 8.6 — Orientation retenue et conséquences

**Cible : Cube**, seul candidat à la fois self-hostable en totalité, sans
dépendance à dbt, doté de jointures à cardinalité déclarée et d'un serveur MCP
pour le LLM. Décision **révisable sans coût** : elle ne devient engageante que le
jour où `render` écrit du YAML Cube.

Conséquences immédiates, faibles par construction :

- `render` (§6) gagne un backend : **modèle sémantique Cube** dérivé de
  `model.json`, aux côtés du Markdown, de `chunks.jsonl` et du payload
  OpenMetadata. Rien ne change en amont.
- Le pivot `model.json` reste **neutre**, et doit le rester. D'abord parce que le
  graphe de jointures pondéré du §2.2 est plus riche que ce qu'un format d'outil
  sait exprimer ; ensuite parce que l'initiative *Open Semantic Interchange*
  (Snowflake, dbt Labs, Salesforce) fait converger ces formats. **On projette
  vers l'outil, on ne modélise pas dans l'outil.**
- Le jeu de paires question → SQL du §7 bis devient encore plus central : lui
  seul dira si le régime « API sémantique » couvre les vraies questions, et
  quelle proportion tombe hors modèle.

### 8.7 — Références

- [MetricFlow passé en open source — dbt Labs](https://www.getdbt.com/blog/open-source-metricflow-governed-metrics)
  et [FAQ du dbt Semantic Layer](https://docs.getdbt.com/docs/use-dbt-semantic-layer/sl-faqs)
  (ce qui reste côté dbt Cloud)
- [Cube Core — dépôt](https://github.com/cube-js/cube),
  [Cube et les agents](https://cube.dev/articles/semantic-layer-for-ai-agents-2026),
  [architecture et limites, revue tierce](https://atlan.com/know/ai-agent/semantic-layer/cube-semantic-layer/)
- [Wren AI OSS](https://www.getwren.ai/oss) et
  [Vanna / WrenAI / DB-GPT comparés](https://sudiptapathak.com/blog/dissecting-open-source-nl2sql/)
- [Panorama des couches sémantiques, 2026](https://dataworkers.io/resources/semantic-layer-tools-compared-2026/)

---

## 9. Journal des révisions

- **2026-09-11** — Ajout du §8 : comparatif des couches sémantiques
  (catalogues, semantic layers, agents text-to-SQL) et orientation vers **Cube**
  comme cible de projection, `model.json` restant le pivot neutre. Conséquences :
  backend supplémentaire pour `render` (§6), question 13 au §7. Le journal des
  révisions passe en §9.
- **2026-09-10 (c)** — Connexion directe confirmée (le code interroge la base,
  pas de forme « script SQL + spool »). BI identifiée : **Tableau**, accès
  probable. Ajout du §2.8 : la couche sémantique Tableau couvre à elle seule la
  priorisation, les libellés humains et le jeu d'évaluation. Questions 6 et 12
  du §7 résolues.
- **2026-09-10 (b)** — Cadre précisé : SGBD **Oracle** ; **pas d'outil d'ETL**
  (« surtout du SQL ») ; **pas de Git côté DBA** ; **logs de requêtes écartés**.
  Conséquences intégrées : §2.3 exploite les statistiques Oracle comme profiling
  gratuit ; §2.5 remplace les logs par cinq substituts de centralité ; §5
  entièrement réécrit pour Oracle ; §7 réorganisé en questions mesurables vs.
  conversationnelles, et ajout du §7 bis sur le critère d'acceptation manquant.
- **2026-09-10 (a)** — Création. Issu d'une discussion sur l'alternative
  « schéma JSON vs templates Jinja », qui a dérivé vers la vraie question : le
  socle déterministe. Accès en lecture sur le datamart confirmé.
