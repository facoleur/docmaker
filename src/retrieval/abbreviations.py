"""Dictionnaire d'abreviations maison : `DMT_CPT_SLD_J` -> "datamart compte solde
journalier". Sans ce depliage, un modele d'embedding generique ne voit que du bruit
dans les identifiants du datamart.

Statut : **fallback transitoire**. La place naturelle de ce vocabulaire est le
glossaire OMD (un GlossaryTerm par abreviation, l'abreviation en `synonyms`), qui
en fait une source de verite unique, relue par catalog.py et editable par les
metiers sans toucher au code. Tant que le glossaire est vide, ce dictionnaire
tient le role ; `expand_identifier` consulte d'abord les synonymes charges depuis
OMD et ne retombe ici que pour les jetons inconnus.
"""

from __future__ import annotations

ABBREVIATIONS = {
    # entites metier
    "CPT": "compte", "CLI": "client", "CRD": "credit", "MVT": "mouvement",
    "SLD": "solde", "OPE": "operation", "DOS": "dossier", "ADR": "adresse",
    "AGE": "agence", "ORG": "organisation", "PM": "personne morale",
    "PP": "personne physique",
    # mesures
    "MT": "montant", "NB": "nombre", "TX": "taux", "TXC": "taux de change",
    "TXI": "taux d'interet", "ENC": "encours", "EXP": "exposition", "TOT": "total",
    "CRE": "credit comptable", "KRI": "indicateur de risque", "RSQ": "risque",
    # temps
    "DT": "date", "JR": "jour", "J": "journalier", "M": "mensuel", "MOIS": "mois",
    "CAL": "calendrier", "TMP": "temps", "ECH": "echeance", "VAL": "validite",
    "DEB": "debut", "FIN": "fin", "OUV": "ouverture", "CHG": "chargement",
    # techniques / referentiel
    "CD": "code", "ID": "identifiant", "LIB": "libelle", "ZON": "zone",
    "TYP": "type", "NAT": "nature", "SEG": "segment", "CAN": "canal",
    "DEV": "devise", "CHF": "franc suisse", "PAY": "pays", "GEO": "geographie",
    "SRC": "source", "LOT": "lot de chargement", "PAR": "parametre",
    "TRT": "traitement", "JRN": "journal", "ACT": "activite",
    "REF": "referentiel", "STG": "staging", "ODS": "donnees operationnelles",
    "DMT": "datamart", "DIM": "dimension", "F": "fait", "D": "dimension",
    "H": "historique",
}


def expand_identifier(identifier: str, extra: dict[str, str] | None = None) -> str:
    """Deplie un identifiant en phrase. `extra` (les synonymes du glossaire OMD)
    est consulte en premier : le vocabulaire gouverne dans OMD prime sur le
    dictionnaire code en dur.
    """
    tokens = identifier.upper().replace(".", "_").split("_")
    lookup = {**ABBREVIATIONS, **(extra or {})}
    return " ".join(lookup.get(token, token.lower()) for token in tokens)
