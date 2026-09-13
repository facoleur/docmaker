"""Parse un fichier .twb (XML Tableau) : extrait les champs calcules, leurs
formules et la connexion source, et propose une description par champ.

NB : `entity_fqn` porte ici le nom du champ calcule Tableau lui-meme (il n'a pas
d'equivalent direct dans OMD tant que Tableau n'y est pas ingere) ; sink/omd.py
rejette proprement ces propositions ("entite introuvable") si aucune entite OMD
ne correspond. Resoudre les references vers les colonnes source (`<cols>`) pour
retrouver une vraie FQN OMD est un prolongement possible, hors perimetre ici.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from src.proposal import Proposal, SourceType


def _connection_ref(root: ET.Element) -> str:
    conn = root.find(".//connection[@dbname]")
    if conn is None:
        return "connexion inconnue"
    return f"{conn.get('server', '?')}/{conn.get('dbname', '?')}"


def parse_twb(path: str) -> list[Proposal]:
    """Genere une Proposal de type description par champ calcule trouve."""
    root = ET.parse(path).getroot()
    ref = _connection_ref(root)
    filename = Path(path).name
    proposals = []

    for column in root.findall(".//column"):
        calc = column.find("calculation")
        formula = calc.get("formula") if calc is not None else None
        if not formula:
            continue
        name = column.get("caption") or column.get("name")
        formula = re.sub(r"\s+", " ", formula).strip()
        proposed = f"Champ calcule Tableau '{name}' (source: {ref}) : {formula}"
        proposals.append(
            Proposal(
                entity_fqn=name,
                entity_type="tableauCalculatedField",
                target_field="description",
                proposed_value=proposed,
                source_type=SourceType.tableau_twb,
                source_ref=filename,
                confidence=0.6,
                confidence_reason=f"formule extraite telle quelle depuis {filename}",
                source_hash=Proposal.compute_hash(proposed),
            )
        )
    return proposals
