"""La boucle de retour manquante (docs/poc-qualite-service.md étape 6) :
`src/sink/omd.py` écrit vers OMD, mais rien ne relit ce qu'un humain corrige
dans l'interface — cette correction n'existait pour personne d'autre. Ce
module lit l'état courant d'un service OMD (GET uniquement, jamais d'écriture)
et le compare au **pivot local** (`build/pivot.json`) pour distinguer :

- **confirmée** — la description est restée identique à la dernière
  proposition, mais elle a été touchée par quelqu'un d'autre que le bot
  (`updatedBy != OMD_BOT_USER`, même convention que `src/sink/omd.py` règle 1) :
  `confidence = 1.0`, `human_touched = True` ;
- **corrigée** — le texte a changé depuis la dernière proposition connue : la
  correction devient la vérité dans le pivot, et l'écart proposition →
  correction est consigné dans `build/feedback.json`. **Ces écarts sont la
  seule trace de la connaissance tacite que le projet existe pour capturer.**

Amorçage : la première fois qu'une entité est vue, il n'y a rien à comparer —
son état courant devient la référence (comportement attendu, pas une anomalie ;
consigné comme tel dans le rapport).

**Note d'implémentation.** `src/sink/omd.py` déclare `lastSourceHash` comme
une custom property de l'entité `table`, pas `column` (voir son docstring) :
un seul hash partagé pour toute la table, écrasé par la dernière colonne
enrichie. Ce module ne peut donc pas s'appuyer sur `lastSourceHash` pour
distinguer les colonnes entre elles — il ne l'utilise que comme **portillon**
(« cette table est-elle passée par le pipeline ? ») et calcule lui-même,
localement, le hash de comparaison par entité (table ou colonne) dans le pivot.

Ne fait que des `GET` vers OMD ; la seule écriture est locale
(`build/pivot.json`, `build/feedback.json`).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from metadata.generated.schema.entity.data.table import Table

from src.eval.omd_client import client_from_env
from src.proposal import Proposal

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
LAST_HASH_PROPERTY = "lastSourceHash"


def _bot_user() -> str:
    return os.environ["OMD_BOT_USER"]


def _stored_hash(table: Table) -> str | None:
    if table.extension and isinstance(table.extension.root, dict):
        return table.extension.root.get(LAST_HASH_PROPERTY)
    return None


def _text(markdown) -> str:
    return markdown.root.strip() if markdown else ""


def _touched_by_human(entity, bot_user: str) -> bool:
    """Même convention que `src/sink/omd.py` règle 1 : `updatedBy` absent = jamais
    encore touché, différent du bot = corrigé ou confirmé par un humain.
    """
    return bool(entity.updatedBy) and entity.updatedBy != bot_user


def _observations(table: Table):
    """Génère (entity_fqn, target_field, description) pour la table elle-même
    puis chacune de ses colonnes décrites.

    Portillon : seules les tables déjà entrées dans le pipeline (`lastSourceHash`
    posé au niveau table) sont considérées — voir la note d'implémentation du
    docstring de ce module sur la portée de cette custom property. OMD n'expose
    l'auteur (`updatedBy`) qu'au niveau table : `compute_feedback` le lit
    directement sur `table`, un seul signal partagé par toutes ses colonnes.
    """
    if _stored_hash(table) is None:
        return
    table_fqn = table.fullyQualifiedName.root
    table_text = _text(table.description)
    if table_text:
        yield table_fqn, "description", table_text
    for column in table.columns:
        col_text = _text(column.description)
        if col_text:
            yield f"{table_fqn}.{column.name.root}", "description", col_text


def load_pivot(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_pivot(pivot: dict[str, dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(pivot, indent=2, ensure_ascii=False, sort_keys=True)
    path.write_text(payload, encoding="utf-8")


def compute_feedback(
    pivot: dict[str, dict], service_tables: list[Table], bot_user: str
) -> tuple[dict[str, dict], list[dict]]:
    """Pure une fois les tables lues : ne fait aucun I/O. Retourne
    `(pivot_mis_a_jour, evenements_de_correction)`.
    """
    # Copie profonde d'un niveau : `entry` est mutée en place plus bas, une
    # copie superficielle partagerait ces dictionnaires avec l'appelant.
    updated = {key: dict(value) for key, value in pivot.items()}
    events: list[dict] = []

    for table in service_tables:
        for entity_fqn, target_field, current_text in _observations(table):
            key = f"{entity_fqn}::{target_field}"
            entry = updated.get(key)
            human_touched = _touched_by_human(table, bot_user)
            current_hash = Proposal.compute_hash(current_text)

            if entry is None:
                updated[key] = {
                    "proposed_value": current_text,
                    "source_hash": current_hash,
                    "confidence": 1.0 if human_touched else 0.5,
                    "human_touched": human_touched,
                    "bootstrapped_at": datetime.now(UTC).isoformat(),
                }
                continue

            if not human_touched:
                continue  # toujours la proposition du bot, rien de nouveau à apprendre

            if current_hash == entry.get("source_hash"):
                entry["confidence"] = 1.0
                entry["human_touched"] = True
                continue

            events.append(
                {
                    "entity_fqn": entity_fqn,
                    "target_field": target_field,
                    "proposed": entry.get("proposed_value", ""),
                    "corrected": current_text,
                    "observed_at": datetime.now(UTC).isoformat(),
                }
            )
            entry["proposed_value"] = current_text
            entry["source_hash"] = current_hash
            entry["confidence"] = 1.0
            entry["human_touched"] = True

    return updated, events


def run(service: str, pivot_path: Path, feedback_path: Path) -> dict:
    client = client_from_env()
    tables = list(
        client.list_all_entities(
            entity=Table, fields=["tags", "columns", "extension"], params={"service": service}
        )
    )
    pivot = load_pivot(pivot_path)
    updated_pivot, events = compute_feedback(pivot, tables, _bot_user())

    save_pivot(updated_pivot, pivot_path)
    if events:
        existing = (
            json.loads(feedback_path.read_text(encoding="utf-8")) if feedback_path.exists() else []
        )
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        feedback_path.write_text(
            json.dumps(existing + events, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return {"n_tables": len(tables), "n_tracked": len(updated_pivot), "n_corrections": len(events)}


def main() -> None:
    load_dotenv(ROOT / ".env")
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", force=True
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", default="DMT_INT", help="Service/datamart OMD à relire")
    parser.add_argument("--pivot", default=str(ROOT / "build" / "pivot.json"))
    parser.add_argument("--feedback", default=str(ROOT / "build" / "feedback.json"))
    args = parser.parse_args()

    summary = run(args.service, Path(args.pivot), Path(args.feedback))
    print(
        f"{summary['n_tables']} table(s) relue(s), {summary['n_tracked']} entrée(s) suivie(s) "
        f"dans le pivot, {summary['n_corrections']} correction(s) consignée(s)"
    )


if __name__ == "__main__":
    main()
