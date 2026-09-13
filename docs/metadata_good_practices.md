## Les abréviations

Ça dépend du type d'abréviation. Il y en a trois sortes, et chacune a sa place.

**Abréviations métier** (MRR, CAC, KYC) : dans le **glossaire**. Le terme porte le nom complet, et l'abréviation va dans le champ synonymes, prévu pour les autres noms du même concept. Exemple : terme « Monthly Recurring Revenue », synonymes `MRR`, `revenu mensuel récurrent`. La doc d'OpenMetadata recommande aussi de mettre le nom développé en display name : CAC devient Customer Acquisition Cost. C'est le point important pour un agent : il retrouve le concept que la question dise « MRR » ou « revenu récurrent ».

**Abréviations techniques dans les noms de colonnes** (`c_id`, `dt_crt`, `amt_ttc`) : deux endroits.

- Sur la colonne elle-même, un display name lisible (`c_id` devient « Customer ID ») et une description. La doc donne justement l'exemple de c_id renommé en Customer ID.
- Les **conventions** de nommage (`_dt` = date, `_amt` = montant, `_flg` = booléen, `stg_` = staging) vont dans **un seul article**. C'est du « comment ça marche », pas des concepts métier. Surtout pas un terme de glossaire pour `_dt`, sinon ton glossaire se remplit de bruit technique.

**Abréviations ambiguës localement** (« CA » veut dire chiffre d'affaires dans la table ventes, mais canton d'Argovie dans la table adresses) : une **memory** attachée à l'asset concerné. C'est exactement le genre de piège qu'une memory doit porter.

## Les tags

D'abord, dans OpenMetadata, « tag » recouvre trois choses différentes :

- **Classification** (PII, Tier, confidentialité) : dit comment **traiter** la donnée.
- **Terme de glossaire** appliqué à un asset : dit de **quoi parle** la donnée.
- **Domaine / Data product** : dit à quel **périmètre** elle appartient.

L'erreur classique est de créer des tags « Finance » ou « Marketing » : c'est le rôle des domaines. Pareil pour le propriétaire, le système source ou la fraîcheur, qui sont déjà des champs dédiés. Ne les duplique pas en tags.

**Le test pour chaque classification :** est-ce qu'elle déclenche une action ? Une politique d'accès, une alerte, un masquage, un filtre de recherche que quelqu'un utilisera vraiment. Si personne ne filtrera ni n'appliquera de règle dessus, ne la crée pas.

**Exclusive ou pas :** une classification peut être marquée mutuellement exclusive. L'asset n'a alors qu'un seul tag du groupe, par exemple tier1 ou tier2 mais pas les deux. Sinon il peut en cumuler plusieurs, comme un client à la fois newCustomer et atRisk. Donc exclusif pour les niveaux (Tier, confidentialité), non exclusif pour les catégories.

**Par type d'objet :**

- **Table** : domaine, owner, un Tier (criticité), le niveau de confidentialité maximum qu'elle contient, et un à trois termes de glossaire pour l'entité principale (« Client », « Commande »). Pas plus.
- **Colonne** : c'est le bon niveau pour la PII. Un terme de glossaire seulement si la colonne représente un vrai concept métier (email, IBAN, MRR). Une colonne `created_at` n'a besoin de rien.
- **Terme de glossaire** : c'est là que se trouve le levier. Quand tu ajoutes un terme de glossaire à un asset, ses tags sont aussi ajoutés à l'asset. Tu tagues donc le terme « Adresse email » une seule fois avec `PII.Sensitive`, et chaque colonne liée à ce terme hérite automatiquement de la classification. Tu classifies le concept, pas 400 colonnes.
- **Articles, documents, memories** : presque rien à taguer à la main. L'essentiel est de les attacher aux bons assets. Côté Collate, ils héritent du domaine et des contrôles d'accès des assets auxquels ils sont liés.

**Un vocabulaire de départ minimal :**

- `Tier` (1 à 5, exclusif) et `PII` (Sensitive / NonSensitive), qui existent par défaut.
- Une classification `Confidentialité` (Public / Interne / Confidentiel / Secret, exclusive). Dans une banque, c'est souvent celle qui compte le plus.
- Éventuellement une classification `Rétention` si tu as des obligations légales de durée de conservation.

Tout le reste devrait passer par le glossaire ou les domaines. Un petit vocabulaire contrôlé, appliqué partout, vaut mieux que cinquante tags libres qui divergent (« Client », « client », « Clients »).

Enfin, pour ne pas tout faire à la main, déclare les termes de glossaire directement dans le code. Avec dbt, tu mets les noms complets des termes dans le `meta` du `schema.yml` et l'ingestion les applique. Les termes doivent exister dans OpenMetadata avant l'ingestion.
