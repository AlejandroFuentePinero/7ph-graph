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
    is drawn; ``by_kind`` is the result's own node-kind distribution and
    ``suggestions`` are narrowing hints derived from it.
    """

    render: bool
    node_count: int
    threshold: int
    by_kind: dict[Kind, int]
    suggestions: list[str]


def assess(subgraph: Subgraph, threshold: int = RENDER_THRESHOLD) -> RenderPlan:
    """Decide whether ``subgraph`` fits under ``threshold`` nodes, or must refine."""
    node_count = len(subgraph.nodes)
    by_kind = dict(Counter(n.kind for n in subgraph.nodes))
    if node_count <= threshold:
        return RenderPlan(True, node_count, threshold, by_kind, [])
    return RenderPlan(False, node_count, threshold, by_kind, _suggestions(by_kind))


def _suggestions(by_kind: dict[Kind, int]) -> list[str]:
    """Narrowing hints derived from the result's own node-kind distribution.

    Names the kind flooding the view (the most numerous) so the user narrows the
    axis that is actually oversized. It stays a general direction rather than
    naming a specific control, because ``assess`` is spec-blind (a pure function
    on a subgraph) and cannot know which filters the active view offers.
    """
    if not by_kind:
        return []
    dominant = max(by_kind, key=lambda k: (by_kind[k], k))
    total = sum(by_kind.values())
    return [
        f"The result is mostly {dominant}s ({by_kind[dominant]} of {total} nodes); "
        "narrow the query to bring it under the limit."
    ]
