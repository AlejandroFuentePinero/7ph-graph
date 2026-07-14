"""Neighbourhood queries against the built graph.

The one query the thin tracer needs: a pilot's neighbourhood, i.e. the decks
they registered and the cards those decks run, shaped as a Subgraph the renderer
can draw. Node ids are namespaced by kind (``pilot:``/``deck:``/``card:``) so a
pilot and a card can never collide on a shared string.
"""

from dataclasses import dataclass
from typing import Literal

import kuzu

from graph7ph.db import rows

Kind = Literal["Pilot", "Deck", "Card"]


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    kind: Kind


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str


@dataclass
class Subgraph:
    nodes: list[Node]
    edges: list[Edge]


def pilot_subgraph(conn: kuzu.Connection, pilot: str) -> Subgraph:
    """The pilot, their decks, and the cards those decks contain."""
    res = conn.execute(
        """MATCH (p:Pilot {pilot: $pilot})<-[:PILOTED_BY]-(d:Deck)
           OPTIONAL MATCH (d)-[c:CONTAINS]->(card:Card)
           RETURN p.pilot, d.deckId, d.name,
                  card.canon, card.name, c.board""",
        {"pilot": pilot},
    )

    nodes: dict[str, Node] = {}
    edges: list[Edge] = []

    for pilot_name, deck_id, deck_name, canon, card_name, board in rows(res):
        pid = f"pilot:{pilot_name}"
        did = f"deck:{deck_id}"
        nodes.setdefault(pid, Node(pid, pilot_name, "Pilot"))
        if did not in nodes:
            nodes[did] = Node(did, deck_name, "Deck")
            edges.append(Edge(did, pid, "PILOTED_BY"))

        if canon is not None:
            cid = f"card:{canon}"
            nodes.setdefault(cid, Node(cid, card_name, "Card"))
            edges.append(Edge(did, cid, f"CONTAINS:{board}"))

    return Subgraph(nodes=list(nodes.values()), edges=edges)
