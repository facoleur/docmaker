"""Seul module autorise a ecrire vers OMD. Un seul appel PATCH par Proposal
(jamais de PUT) ; aucun autre module de ce pipeline ne doit toucher l'API en
ecriture.

write_proposal applique 4 regles dans l'ordre strict et s'arrete (skip, avec
raison loguee) des qu'une bloque :
  1. `Table.updatedBy` != OMD_BOT_USER -> "human_touched" (une correction
     humaine ne doit jamais etre ecrasee).
  2. `source_hash` deja stocke dans la custom property `lastSourceHash` ->
     "unchanged" (pas de reecriture inutile).
  3. Patch uniquement le `target_field` concerne (description de table/colonne,
     ou tag/glossaryTerm) sur une copie profonde de l'entite.
  4. Marque la proposition comme non confirmee dans le MEME patch : un tag ou
     un terme de glossaire est ajoute avec `TagLabel.state=Suggested` (etat OMD
     natif "propose par un outil, a confirmer par le proprietaire" - il n'y a
     pas de champ "Draft" separe sur un TagLabel) ; une description est
     accompagnee du tag de revue `OMD_REVIEW_TAG_FQN`. `lastSourceHash` est mis
     a jour dans la meme extension.

Le diff source/destination -> JSON Patch est delegue a `client.patch()`, le
meme mecanisme que celui des `patch_description`/`patch_column_tags` officiels
du SDK (verifie dans metadata.ingestion.ometa.mixins.patch_mixin).

Prerequis operationnel (a faire cote OMD, ce module ne le fait pas) : la custom
property `lastSourceHash` (type string) doit deja etre declaree sur le type
d'entite `table` (Settings > Custom Properties).

Piege de bootstrap a connaitre : `OMD_BOT_USER` doit correspondre a l'identite
qui a legitimement ecrit l'etat courant de l'entite (le bot d'ingestion Oracle,
si c'est lui qui a cree la table et qu'aucune enrichissement n'a encore eu
lieu), sans quoi la regle 1 bloquerait indefiniment les tables jamais encore
enrichies. A verifier avec l'equipe DBA selon les comptes de service reels.
"""

from __future__ import annotations

import logging
import os
from collections import Counter

from metadata.generated.schema.entity.data.table import Table
from metadata.generated.schema.type.basic import EntityExtension, Markdown
from metadata.generated.schema.type.tagLabel import LabelType, State, TagLabel, TagSource
from metadata.ingestion.ometa.ometa_api import OpenMetadata

from src.eval.omd_client import client_from_env
from src.proposal import Proposal

logger = logging.getLogger(__name__)

LAST_HASH_PROPERTY = "lastSourceHash"
SUPPORTED_ENTITY_TYPES = ("table", "column")


def _bot_user() -> str:
    return os.environ["OMD_BOT_USER"]


def _review_tag_fqn() -> str:
    return os.environ.get("OMD_REVIEW_TAG_FQN", "IA – à valider")


def _split_fqn(proposal: Proposal) -> tuple[str, str | None]:
    """Table -> (table_fqn, None) ; Colonne -> (table_fqn, column_fqn complet)."""
    if proposal.entity_type == "column":
        table_fqn, _, _ = proposal.entity_fqn.rpartition(".")
        return table_fqn, proposal.entity_fqn
    return proposal.entity_fqn, None


def _stored_hash(table: Table) -> str | None:
    if table.extension and isinstance(table.extension.root, dict):
        return table.extension.root.get(LAST_HASH_PROPERTY)
    return None


def _find_column(table: Table, column_fqn: str):
    return next(
        (
            c
            for c in table.columns
            if c.fullyQualifiedName and c.fullyQualifiedName.root.lower() == column_fqn.lower()
        ),
        None,
    )


def _apply_target_field(destination: Table, proposal: Proposal, column_fqn: str | None) -> bool:
    """Mute `destination` en place : target_field + marqueur de revue + hash.

    False si la colonne visee n'existe pas.
    """
    holder = destination
    if column_fqn:
        holder = _find_column(destination, column_fqn)
        if holder is None:
            return False

    if proposal.target_field == "description":
        holder.description = Markdown(proposal.proposed_value)
        review = TagLabel(
            tagFQN=_review_tag_fqn(),
            source=TagSource.Classification,
            labelType=LabelType.Automated,
            state=State.Suggested,
        )
        holder.tags = [*(holder.tags or []), review]
    else:  # "tag" ou "glossaryTerm" : meme mecanisme, source TagLabel differente
        source = (
            TagSource.Glossary
            if proposal.target_field == "glossaryTerm"
            else TagSource.Classification
        )
        label = TagLabel(
            tagFQN=proposal.proposed_value,
            source=source,
            labelType=LabelType.Automated,
            state=State.Suggested,
        )
        holder.tags = [*(holder.tags or []), label]

    extension = dict(destination.extension.root) if destination.extension else {}
    extension[LAST_HASH_PROPERTY] = proposal.source_hash
    destination.extension = EntityExtension(extension)
    return True


def write_proposal(proposal: Proposal, client: OpenMetadata | None = None) -> tuple[bool, str]:
    """Applique une Proposal. Retourne (ecrit, raison) ; ne leve pas pour un skip normal."""
    client = client or client_from_env()

    if proposal.entity_type not in SUPPORTED_ENTITY_TYPES:
        return False, f"entity_type non supporte: {proposal.entity_type}"

    table_fqn, column_fqn = _split_fqn(proposal)
    table = client.get_by_name(entity=Table, fqn=table_fqn, fields=["tags", "columns", "extension"])
    if table is None:
        logger.info("skip entite introuvable: %s", proposal.entity_fqn)
        return False, "entite introuvable"

    bot_user = _bot_user()
    if table.updatedBy and table.updatedBy != bot_user:
        logger.info("skip human_touched: %s (updatedBy=%s)", proposal.entity_fqn, table.updatedBy)
        return False, "human_touched"

    if _stored_hash(table) == proposal.source_hash:
        logger.info("skip unchanged: %s", proposal.entity_fqn)
        return False, "unchanged"

    destination = table.model_copy(deep=True)
    if not _apply_target_field(destination, proposal, column_fqn):
        logger.info("skip colonne introuvable: %s", proposal.entity_fqn)
        return False, "colonne introuvable"

    updated = client.patch(entity=Table, source=table, destination=destination)
    if updated is None:
        logger.warning("echec du patch: %s", proposal.entity_fqn)
        return False, "erreur_patch"

    logger.info(
        "ecrit: %s.%s = %r", proposal.entity_fqn, proposal.target_field, proposal.proposed_value
    )
    return True, "ecrit"


def write_batch(proposals: list[Proposal], client: OpenMetadata | None = None) -> dict:
    """Traite une liste de Proposal. Retourne {written, errors, skipped: {raison: n}}."""
    client = client or client_from_env()
    written = errors = 0
    skipped: Counter[str] = Counter()

    for proposal in proposals:
        try:
            ok, reason = write_proposal(proposal, client=client)
        except Exception:
            logger.exception("erreur en ecrivant %s", proposal.entity_fqn)
            errors += 1
            continue
        if ok:
            written += 1
        else:
            skipped[reason] += 1

    return {"written": written, "errors": errors, "skipped": dict(skipped)}
