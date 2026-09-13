# Architecture Source → Enrich → Sink

## Le pattern en une phrase

C'est le pattern **ETL en flux unidirectionnel** (aussi appelé _pipeline en entonnoir_) : plusieurs producteurs hétérogènes alimentent un **contrat de données commun** (`Proposal`), et un **seul** consommateur a le droit d'écrire l'état final. Aucun producteur ne connaît la destination ; seul le consommateur final connaît les règles d'écriture.

## Les trois rôles

### `src/sources/` — les producteurs

Chaque fichier extrait un signal d'une origine différente (conventions SQL minées via l'usage OMD, fichiers `.twb` Tableau, dictionnaire Oracle...) et le traduit en `Proposal`. **Aucun ne sait ce qui va se passer après** — pas d'appel OMD en écriture, pas de logique de décision. Ajouter une nouvelle source = ajouter un fichier, zéro impact sur le reste.

### `src/enrich/` — un producteur spécial (LLM)

Même contrat de sortie (`Proposal`), mais isolé des autres parce que :

- il est **coûteux** (appel LLM, latence),
- il tourne **offline en batch**, jamais synchrone avec l'ingestion,
- il ne doit **jamais** être déclenché automatiquement par le pipeline OMD lui-même (risque de boucle / de charge incontrôlée).

### `src/sink/omd.py` — le seul point d'écriture

Reçoit des `Proposal`, applique les règles de sécurité (ne jamais écraser une correction humaine, ne jamais réécrire un contenu inchangé) et **seul lui** appelle l'API OMD en PATCH. C'est le goulot d'étranglement _voulu_ : toute la logique de sécurité/audit vit à un seul endroit, testable isolément avec des mocks.

## Le contrat commun : `Proposal`

C'est l'équivalent d'un **schéma d'événement** (comme un schéma Avro/Protobuf dans Kafka) : tous les producteurs doivent produire exactement cette forme (`entity_fqn`, `target_field`, `proposed_value`, `source_hash`, `confidence`...). Le sink ne sait rien de SQLGlot, Tableau ou d'Ollama — il ne connaît que `Proposal`. C'est ce découplage qui permet d'ajouter une source sans toucher au sink, et de tester le sink sans jamais lancer une vraie source.

## Le parallèle avec Kafka / Kafka Connect

| Kafka Connect                             | Ce pipeline                                    |
| ----------------------------------------- | ---------------------------------------------- |
| **Source connector** (DB → topic)         | `src/sources/*.py`, `src/enrich/describe.py`   |
| **Message schéma** (Avro/Protobuf)        | `Proposal` (Pydantic)                          |
| **Topic** (buffer découplé)               | la liste de `Proposal` collectée par `run.py`  |
| **Sink connector** (topic → DB/API cible) | `src/sink/omd.py`                              |
| **Idempotency key / dedup**               | `source_hash` (évite les réécritures)          |
| **Offset / dernier état connu**           | `Table.updatedBy` + `extension.lastSourceHash` |

Dans Kafka, un _source connector_ ne parle jamais directement à un _sink connector_ — ils communiquent uniquement via le topic (le schéma commun). Ici, `sources/` et `enrich/` ne parlent jamais directement à OMD — ils communiquent uniquement via des objets `Proposal` que `run.py` collecte puis transmet au sink.

## Pourquoi c'est ce découpage et pas un script monolithique

1. **Sécurité concentrée** : la règle « jamais écraser une correction humaine » vit à un seul endroit audité, pas dupliquée dans chaque source.
2. **Testabilité** : le sink se teste à 100% avec des mocks, sans jamais toucher SQLGlot/XML/LLM. Les sources se testent sans jamais toucher OMD.
3. **Extensibilité sans risque** : ajouter une 4ᵉ source (ex. dictionnaire Oracle) ne peut _structurellement pas_ introduire un bug d'écriture, puisque ce module n'a même pas accès à la fonction de patch.
4. **Auditabilité** : face à un DBA qui conteste une écriture, il n'y a qu'un seul endroit à montrer (`sink/omd.py`) pour justifier _pourquoi_ une valeur a été écrite.

C'est le même raisonnement qui a motivé le nom **`sink`** : vocabulaire standard ETL pour « le point où le flux se termine », par opposition à **`source`** (le point d'entrée).
