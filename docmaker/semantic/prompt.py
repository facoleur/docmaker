"""prompt : budget de contexte à deux niveaux — docs/poc-qualite-service.md étape 7.

Sur des centaines d'entités, tout injecter dépasse le budget d'un modèle
≤ 30B. Hiérarchie à deux niveaux :

- **niveau 1**, toujours présent : le sommaire de toutes les entités (nom, ce
  qu'elle décrit, ses mesures) — quelques milliers de tokens ;
- **niveau 2**, à la demande : le détail des entités retenues, sélectionnées
  soit parce qu'une métrique citée dans la question pointe dessus (+ ses
  voisines à distance 1 dans le graphe de jointures), soit par un score
  lexical sur le sommaire.

Aucun tokenizer réel n'est ajouté comme dépendance pour ce POC : le compte de
tokens est une approximation `len(texte) // 4`, documentée comme telle — elle
suffit à mesurer un ordre de grandeur et une tendance, pas une valeur exacte.
Une base vectorielle ne se justifie que si la mesure du taux de sélection
correcte, en aval, montre que ce routage lexical échoue (voir `docs/
poc-qualite-service.md` étape 7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import Joins
from .model import SemanticModel

_WORD_RE = re.compile(r"[a-zà-ÿ0-9_]+")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def summary(model: SemanticModel) -> str:
    lines = []
    for name, entity in sorted(model.entities.items()):
        measures = ", ".join(sorted(entity.measures)) or "(aucune)"
        grain = entity.grain or "(maille non documentée)"
        lines.append(f"- {name} : {grain} — mesures : {measures}")
    return "\n".join(lines)


def detail(model: SemanticModel, names: list[str]) -> str:
    blocks = []
    for name in names:
        entity = model.entities.get(name)
        if not entity:
            continue
        dims = ", ".join(sorted(entity.dimensions)) or "(aucune)"
        measures = ", ".join(sorted(entity.measures)) or "(aucune)"
        filters = "; ".join(entity.default_filters) or "(aucun)"
        blocks.append(
            f"### {name}\n"
            f"table racine : {entity.root}\n"
            f"satellites : {', '.join(entity.satellites) or '(aucun)'}\n"
            f"clé : {entity.key or '(non documentée)'} — maille : {entity.grain or '?'}\n"
            f"dimensions : {dims}\nmesures : {measures}\nfiltres par défaut : {filters}\n"
            f"confiance : {entity.confidence}"
        )
    return "\n\n".join(blocks)


def select_entities(question: str, model: SemanticModel, limit: int = 5) -> list[str]:
    """Score lexical simple : proportion de tokens de la question retrouvés dans
    le nom, la maille et les mesures/dimensions de chaque entité. Approche
    volontairement écartée d'une base vectorielle (voir docstring du module).
    """
    q_tokens = set(_WORD_RE.findall(question.lower()))
    if not q_tokens:
        return []
    scored = []
    for name, entity in model.entities.items():
        haystack = " ".join(
            [name, entity.grain, *entity.dimensions, *entity.measures]
        ).lower()
        e_tokens = set(_WORD_RE.findall(haystack))
        overlap = len(q_tokens & e_tokens)
        if overlap:
            scored.append((overlap, name))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [name for _, name in scored[:limit]]


def neighbors(model: SemanticModel, name: str, joins: Joins) -> list[str]:
    """Entités à distance 1 dans le graphe de jointures vérifié, via leur
    table racine ou une de leurs satellites.
    """
    entity = model.entities.get(name)
    if not entity:
        return []
    tables = {entity.root, *entity.satellites}
    connected_tables: set[str] = set()
    for c in joins.candidates:
        if not c.included:
            continue
        left_t, right_t = c.left.rsplit(".", 1)[0], c.right.rsplit(".", 1)[0]
        if left_t in tables:
            connected_tables.add(right_t)
        if right_t in tables:
            connected_tables.add(left_t)
    connected_tables -= tables

    result = []
    for other_name, other in model.entities.items():
        if other_name != name and ({other.root, *other.satellites} & connected_tables):
            result.append(other_name)
    return sorted(result)


@dataclass
class ContextResult:
    text: str
    n_tokens: int
    level: int  # 1 = sommaire seul, 2 = sommaire + détail
    selected_entities: list[str] = field(default_factory=list)


def _summary_only(base: str) -> ContextResult:
    return ContextResult(text=base, n_tokens=estimate_tokens(base), level=1, selected_entities=[])


def _with_detail(text: str, entities: list[str]) -> ContextResult:
    return ContextResult(
        text=text, n_tokens=estimate_tokens(text), level=2, selected_entities=entities
    )


def build_context(
    question: str, model: SemanticModel, joins: Joins, max_tokens: int
) -> ContextResult:
    base = summary(model)
    selected = select_entities(question, model)
    for name in list(selected):
        selected += [n for n in neighbors(model, name, joins) if n not in selected]

    if not selected:
        return _summary_only(base)

    text = base + "\n\n" + detail(model, selected)
    if estimate_tokens(text) <= max_tokens:
        return _with_detail(text, selected)

    # Dépassement du budget : on réduit le détail aux seules entités
    # directement sélectionnées (sans les voisines), avant de retomber au
    # sommaire seul si ça ne suffit toujours pas.
    direct = select_entities(question, model)
    text = base + "\n\n" + detail(model, direct)
    if estimate_tokens(text) > max_tokens:
        return _summary_only(base)
    return _with_detail(text, direct)
