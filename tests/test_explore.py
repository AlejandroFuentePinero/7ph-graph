from graph7ph.explore import RENDER_THRESHOLD, assess
from graph7ph.query import Node, Subgraph


def _cards(n: int, kind: str = "Card") -> Subgraph:
    """A subgraph of ``n`` nodes of one kind, no edges."""
    return Subgraph(nodes=[Node(f"{kind}:{i}", str(i), kind) for i in range(n)], edges=[])


def test_a_subgraph_within_the_threshold_renders():
    plan = assess(_cards(5), threshold=10)

    assert plan.render is True
    assert plan.node_count == 5
    assert plan.suggestions == []


def test_a_subgraph_exactly_at_the_threshold_still_renders():
    plan = assess(_cards(10), threshold=10)

    assert plan.render is True


def test_a_subgraph_over_the_threshold_refines_rather_than_renders():
    plan = assess(_cards(11), threshold=10)

    assert plan.render is False
    assert plan.node_count == 11
    assert plan.threshold == 10


def test_refine_reports_the_results_own_node_kind_distribution():
    # A card-usage-shaped flood: one card, many decks, each with its pilot.
    sub = Subgraph(
        nodes=(
            [Node("card:x", "X", "Card")]
            + [Node(f"deck:{i}", str(i), "Deck") for i in range(40)]
            + [Node(f"pilot:{i}", str(i), "Pilot") for i in range(20)]
        ),
        edges=[],
    )

    plan = assess(sub, threshold=10)

    assert plan.render is False
    assert plan.by_kind == {"Card": 1, "Deck": 40, "Pilot": 20}


def test_refine_suggestion_names_the_kind_flooding_the_view():
    # Decks dominate this result, so the narrowing hint is about cutting decks.
    sub = Subgraph(
        nodes=(
            [Node("card:x", "X", "Card")]
            + [Node(f"deck:{i}", str(i), "Deck") for i in range(40)]
        ),
        edges=[],
    )

    plan = assess(sub, threshold=10)

    joined = " ".join(plan.suggestions).lower()
    assert "deck" in joined  # the dominant kind is named
    assert "40" in joined  # its count, drawn from the distribution


def test_an_empty_result_renders_the_empty_graph():
    plan = assess(Subgraph(nodes=[], edges=[]), threshold=10)

    assert plan.render is True
    assert plan.by_kind == {}
    assert plan.suggestions == []


def test_the_default_threshold_is_the_single_config_constant():
    # No threshold passed: the render-vs-refine line is RENDER_THRESHOLD, and a
    # result one node over it refines.
    assert isinstance(RENDER_THRESHOLD, int) and RENDER_THRESHOLD > 0
    assert assess(_cards(RENDER_THRESHOLD)).render is True
    assert assess(_cards(RENDER_THRESHOLD + 1)).render is False
