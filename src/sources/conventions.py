"""Mine les conventions implicites depuis les requetes capturees par l'agent Usage
d'OMD (`Query.query`, associees a une table par `get_entity_queries`).

Pour chaque colonne, calcule la frequence des predicats (operateur, valeur) vus
dans les WHERE / conditions de JOIN ; au-dessus du seuil, propose une description
qui documente la convention. Ne fait que du GET vers OMD (aucune ecriture).
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

import sqlglot
from metadata.generated.schema.entity.data.table import Table
from sqlglot import exp

from src.eval.omd_client import client_from_env
from src.proposal import Proposal, SourceType

logger = logging.getLogger(__name__)

_COMPARISONS = {exp.EQ: "=", exp.NEQ: "!=", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<="}


def _predicates(sql: str) -> list[tuple[str, str, str]]:
    """Extrait les (colonne, operateur, valeur) des WHERE et conditions de JOIN."""
    tree = sqlglot.parse_one(sql, read="oracle", error_level=sqlglot.ErrorLevel.RAISE)
    found = []
    for clause in (*tree.find_all(exp.Where), *tree.find_all(exp.Join)):
        for node_type, op in _COMPARISONS.items():
            for node in clause.find_all(node_type):
                col, lit = node.left, node.right
                if isinstance(col, exp.Column) and isinstance(lit, exp.Literal):
                    found.append((col.name, op, lit.this))
    return found


def mine_conventions(service_name: str, threshold: float = 0.8) -> tuple[list[Proposal], float]:
    """Retourne (propositions minees, taux de requetes non parsables par SQLGlot)."""
    client = client_from_env()
    proposals: list[Proposal] = []
    total_queries = 0
    unparsable = 0

    for table in client.list_all_entities(entity=Table, params={"service": service_name}):
        table_fqn = table.fullyQualifiedName.root
        counters: dict[str, Counter] = defaultdict(Counter)
        n_queries = 0

        for query in client.get_entity_queries(entity_id=table.id, fields=["query"]) or []:
            n_queries += 1
            total_queries += 1
            try:
                for column, op, value in _predicates(query.query.root):
                    counters[column][(op, value)] += 1
            except Exception:
                unparsable += 1
                logger.debug("requete non parsable sur %s", table_fqn)

        if not n_queries:
            continue

        for column, counts in counters.items():
            (op, value), n = counts.most_common(1)[0]
            freq = n / n_queries
            if freq < threshold:
                continue
            proposed = (
                f"Colonne filtree a {op} {value!r} dans {freq:.0%} des requetes observees "
                f"sur {table.name.root} (convention deduite de l'usage)."
            )
            proposals.append(
                Proposal(
                    entity_fqn=f"{table_fqn}.{column}",
                    entity_type="column",
                    target_field="description",
                    proposed_value=proposed,
                    source_type=SourceType.sql_parsing,
                    source_ref=f"{n_queries} requetes usage sur {table_fqn}",
                    confidence=round(freq, 2),
                    confidence_reason=f"present dans {freq:.0%} des requetes sur {table.name.root}",
                    source_hash=Proposal.compute_hash(proposed),
                )
            )

    rate = unparsable / total_queries if total_queries else 0.0
    logger.info(
        "requetes non parsables par SQLGlot : %.1f%% (%d/%d)", rate * 100, unparsable, total_queries
    )
    return proposals, rate
