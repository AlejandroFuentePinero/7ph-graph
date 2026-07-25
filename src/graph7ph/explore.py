"""Render-vs-refine: decide whether a query's subgraph is small enough to draw.

A result too large to read is never rendered or truncated (user stories 20-22);
instead the explorer alerts and suggests narrowing the query, using the result's
own node-kind distribution to say what is flooding the view. ``RENDER_THRESHOLD``
is the single tunable that draws the render-vs-refine line (issue #7).
"""

from collections import Counter
from dataclasses import dataclass

from graph7ph.query import Kind, Subgraph

# The most nodes the explorer will draw before a result stops reading as a graph
# and becomes a hairball. A card's handful of packages or a pilot's deck or two
# sit under it; a prolific pilot's whole neighbourhood or a staple card across
# hundreds of decks blows past it and is refined instead (a full 75-card deck is
# already ~76 nodes). Tunable: the one place the render-vs-refine line is set.
RENDER_THRESHOLD = 250


@dataclass(frozen=True)
class RenderPlan:
    """The decision for one result: draw it, or refine because it is too big.

    ``render`` is True when the subgraph fits the threshold. When False nothing
    is drawn; ``by_kind`` is the result's own node-kind distribution, from which the
    too-large state names the axis to narrow (:func:`dominant_kind`).
    """

    render: bool
    node_count: int
    threshold: int
    by_kind: dict[Kind, int]


def assess(subgraph: Subgraph, threshold: int = RENDER_THRESHOLD) -> RenderPlan:
    """Decide whether ``subgraph`` fits under ``threshold`` nodes, or must refine."""
    node_count = len(subgraph.nodes)
    by_kind = dict(Counter(n.kind for n in subgraph.nodes))
    return RenderPlan(node_count <= threshold, node_count, threshold, by_kind)


def dominant_kind(by_kind: dict[Kind, int]) -> Kind:
    """The node kind flooding a result: the most numerous, ties broken by name so the
    pick is deterministic. The too-large state message (:func:`graph7ph.app._refine_alert`)
    names the oversized axis from this. Assumes a non-empty distribution (its caller only
    reaches it for a result over the render threshold, which therefore has nodes)."""
    return max(by_kind, key=lambda k: (by_kind[k], k))
