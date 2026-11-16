"""Render a Subgraph to an interactive pyvis widget (HTML string).

The widget names the vis.js library the app serves itself rather than carrying a
copy, so a result is a few KB and the library is downloaded once (issue #97).
The document is the graph alone, filling its frame (issue #330). The colour key and
the answer to a node click are the page's, in a panel beside the frame: this module
hands the page the key (:func:`colour_key`) and each node's colour
(:func:`node_colours`), and its document tells that panel which node was clicked
(:data:`_ANSWER`).

pyvis's own generated graph internals (its node/edge JS, its layout) are deliberately
not asserted, per the PRD testing plan. What this module *injects onto* that document
is: the tests hold the shared-palette node colours, the colour key, the dark
ground, the count hover on a visible rate edge (#211),
the fit-and-label settle (:data:`_SETTLE`), the mark on the node a graph was asked
about (:data:`SUBJECT_SIZE`) and the focus a hovered or selected node takes
(:data:`_FOCUS`, #331), since those are this module's contribution and the visual-direction contract (§7) they
answer to.

One part of that contribution cannot be held in the emitted string at all. Whether a
label reaches the reader is decided at paint time, out of the zoom a fit lands on and
the frame it lands in, and #178 is what happens when nobody is watching those numbers:
labels merely small at desktop width, one dense view from vis.js dropping them.
``tests/test_graph_desktop.py`` draws these documents on a real browser at desktop
width and measures them there.
"""

import json
import re
from collections.abc import Collection

from pyvis.network import Network

from graph7ph.numfmt import count_of
from graph7ph.palette import CATEGORICAL, assign
from graph7ph.query import Node, Subgraph
from graph7ph.serve import LABEL_FACE_URL, VIS_CSS_URL, VIS_JS_URL
from graph7ph.theme import FONT_STACK, FONTS, TOKENS

# §7: the eight node kinds take the eight palette slots in a fixed by-kind order,
# so the graph draws its colour from the one shared vocabulary (`assign`) the
# charts also use. Deck leads, so it lands on slot 1 (blue), the one worked example
# §5 names ("a Deck is the same blue as a dot or a line"). Colour is a secondary
# cue: the node label plus the on-screen key carry identity, never hue alone, since
# any two kinds can sit adjacent.
_KIND_ORDER = (
    "Deck", "Pilot", "Card", "Archetype", "Macro", "Event", "Placement", "Intersection",
)
_KIND_COLOURS = assign(_KIND_ORDER)

# What a reader is told a kind is, where that is not the kind's own name. No surface
# prints the word "Macro" (CONTEXT.md): it is a broad class to anyone reading.
_KIND_NAMES = {"Macro": "Broad class"}


def kind_name(kind: str) -> str:
    """A node kind as a reader is told it: on the colour key, and on a hover."""
    return _KIND_NAMES.get(kind, kind)


# Grouped views colour by group instead of by kind, so the groups read apart at
# a glance: a head-to-head tints each player's chain, and card co-occurrence
# tints each seed card and its shared partners. Slots 1-3 of the shared set, the
# trio that stays distinct under adjacency (§7); up to three groups present (two
# seeds and their partners).
_GROUP_COLOURS = CATEGORICAL[:3]

# A deck's stable id is its Moxfield public id, so its authoritative list is a
# direct construction (confirmed against the source's own ``url`` field).
_MOXFIELD = "https://moxfield.com/decks/{}"


def moxfield_url(deck_id: str) -> str:
    """The Moxfield page for a deck, by its id."""
    return _MOXFIELD.format(deck_id)


def _hosted_library(doc: str) -> str:
    """Point the widget's two library tags at the copy the app serves.

    pyvis offers its library inlined or from a CDN and nothing in between, so the
    widget is generated in its CDN shape and the tags are rewritten here. They are
    rebuilt rather than their URLs substituted, because pyvis's carry a Subresource
    Integrity hash for the CDN's bytes that a browser would then check ours against.

    The URLs are root-relative, which holds the app to being served at the root of
    its origin. It is, on all three ways it runs: the Space, ``graph7ph app``, and
    Colab, where ``proxyPort`` hands the notebook a per-port hostname of its own.
    Mounting it under a path (a ``root_path``, or ``mount_gradio_app`` inside a
    larger API) would break this and nothing else, silently: the widget is embedded
    through an iframe ``srcdoc``, which has no URL of its own, so these resolve
    against the parent document, and a prefix the parent carries is not in them.
    """
    tags = (
        (r"<link[^>]*cdnjs[^>]*vis-network[^>]*>",
         f'<link rel="stylesheet" href="{VIS_CSS_URL}"/>'),
        (r"<script[^>]*cdnjs[^>]*vis-network[^>]*></script>",
         f'<script src="{VIS_JS_URL}"></script>'),
    )
    for pattern, tag in tags:
        doc, found = re.subn(pattern, tag, doc)
        if found != 1:
            raise RuntimeError(
                f"pyvis emitted {found} tags matching {pattern}, expected 1: its "
                "template has changed, and the graph would draw with no library."
            )
    return doc


# pyvis's template also loads Bootstrap, a stylesheet and a script, from a CDN. The
# graph uses none of its components and rested on three of its rules, which
# `_DOC_STYLE` now states itself, so both tags are dropped and a visitor's browser
# asks nobody but the app for anything (issue #312).
_BOOTSTRAP_TAGS = (
    r"<link[^>]*jsdelivr[^>]*bootstrap[^>]*>",
    r"<script[^>]*jsdelivr[^>]*bootstrap[^>]*>\s*</script>",
)

# A file the document would have the browser fetch from another host.
_OUTSIDE_FILE = re.compile(
    r'<(?:link|script)\b[^>]*\b(?:href|src)\s*=\s*"((?:https?:)?//[^"]*)"'
)


def _nothing_from_outside(doc: str) -> str:
    """Drop pyvis's Bootstrap tags, and refuse a document that loads anything else
    from another host.

    The tags are removed by name, so on their own they would let a later pyvis bring
    a third party back unnoticed. Whatever outside file is left once they are gone
    is one this module has never seen, and the render stops on it by name.
    """
    for pattern in _BOOTSTRAP_TAGS:
        doc, found = re.subn(pattern, "", doc)
        if found != 1:
            raise RuntimeError(
                f"pyvis emitted {found} tags matching {pattern}, expected 1: its "
                "template has changed, and the graph may be loading Bootstrap again."
            )
    if outside := _OUTSIDE_FILE.findall(doc):
        raise RuntimeError(
            f"pyvis's document loads {outside} from outside the app: its template "
            "has changed. Serve the file from the app (`serve.py`) or drop the tag."
        )
    return doc


# A group whose members are a set rather than one named thing, named for what the set
# means. The co-occurrence view buckets every partner card under one group value, so
# there is nothing in it to take a name from; the head-to-head and single-seed groups
# both name themselves (below) and need no entry.
_GROUP_NAMES = {"cooccur": "Played alongside"}


def _group_name(group: str, nodes: list[Node]) -> str | None:
    """What the colour key calls a group, or ``None`` if it cannot be named honestly.

    A group's value is an internal id, never a thing to show a reader, so the key has
    to recover a name for it. Three cases, in order:

    - Its **anchor node**, the member whose id *is* the group value: the pilot in a
      head-to-head, whose display name is the group's name.
    - Its **sole member**, for a group with no anchor but only one thing in it: a
      co-occurrence ``seed:`` bucket holds exactly the seed card, so the card names it.
    - A **written name** for a bucket that is a set, from :data:`_GROUP_NAMES`.

    The middle two are why this exists: the co-occurrence views' groups have no anchor
    (their card ids never equal a ``seed:``/``cooccur`` value), and the key used to
    drop every group it could not anchor, which on those views meant dropping all of
    them and drawing no key at all, on the one view where colour is doing the most work.

    Takes the nodes and does its own scan rather than being handed lookup tables built
    for it: at most three groups over a subgraph the renderer refuses past 250 nodes,
    so the walk is free, and one argument cannot fall out of step with another.
    """
    members = [n for n in nodes if n.group == group]
    for node in members:
        if node.id == group:
            return node.label
    if len(members) == 1:
        return members[0].label
    return _GROUP_NAMES.get(group)


def _group_palette(nodes: list[Node]) -> dict[str, str]:
    """A stable colour per group present, in group order, so two chains stay distinct."""
    groups = sorted({n.group for n in nodes if n.group is not None})
    return {g: _GROUP_COLOURS[i % len(_GROUP_COLOURS)] for i, g in enumerate(groups)}


def node_colours(subgraph: Subgraph) -> dict[str, str]:
    """The colour each node is drawn in, by node id: its group's in a grouped view,
    otherwise its kind's. For whatever names a node beside the graph and wants the
    dot it is drawn as."""
    palette = _group_palette(subgraph.nodes)
    return {
        n.id: palette[n.group] if n.group is not None else _KIND_COLOURS.get(n.kind)
        for n in subgraph.nodes
    }


def colour_key(subgraph: Subgraph) -> list[tuple[str, str]]:
    """What colour encodes in this graph, as ``(name, colour)`` pairs: the groups in
    a grouped view, otherwise the kinds present, in the fixed slot order.

    Only what is actually drawn is keyed, so identity is carried by a name beside
    its swatch and never by hue alone (§7). A name can be untrusted (a group is a
    player or a card), so whoever draws the key escapes it.
    """
    palette = _group_palette(subgraph.nodes)
    if palette:
        return [
            (name, colour) for group, colour in palette.items()
            if (name := _group_name(group, subgraph.nodes))
        ]
    kinds_present = {n.kind for n in subgraph.nodes}
    return [(kind_name(k), _KIND_COLOURS[k]) for k in _KIND_ORDER if k in kinds_present]


def _tied(subgraph: Subgraph, subjects: Collection[str]) -> set[str]:
    """The subjects the layout pulls onto another subject: those that share a
    neighbour with one, as the two pilots of a head-to-head share every event. They
    are the ones given weight (:data:`SUBJECT_MASS`). Two subjects with nothing
    between them, two cards that share no deck, are held near by nothing, and weight
    would only throw them apart."""
    around: dict[str, set[str]] = {subject: set() for subject in subjects}
    for edge in subgraph.edges:
        for end, other in ((edge.source, edge.target), (edge.target, edge.source)):
            if end in around:
                around[end].add(other)
    return {a for a in around for b in around if a != b and around[a] & around[b]}


def render_subgraph(subgraph: Subgraph, subjects: Collection[str] = ()) -> str:
    """The graph as a document. ``subjects`` are the ids of the nodes the graph was
    asked about (a pilot, a card), which are drawn to stand out (:data:`SUBJECT_SIZE`).
    """
    # Both dimensions are the frame's, not a number of our own: `_DOC_STYLE` lays the
    # document out as a full-height flex column and the graph fills it, so the height
    # the embedding iframe is given (`app._embed`) is the height.
    net = Network(
        height="100%", width="100%", directed=True, cdn_resources="remote",
        bgcolor=TOKENS["surface"], font_color=TOKENS["text"],
    )
    palette = _group_palette(subgraph.nodes)
    colours = node_colours(subgraph)
    group_by_id = {n.id: n.group for n in subgraph.nodes}
    heavy = _tied(subgraph, subjects)
    for node in subgraph.nodes:
        # A weighted node is sized by its value; vis.js scales the values in the
        # graph between a min and max radius, so bigger weight reads as a bigger
        # node. Unweighted nodes render at the default size.
        weighted = {"value": node.weight} if node.weight is not None else {}
        # A shape override (e.g. "circle") draws the label inside the node; vis.js
        # then sizes it to the text, so a shaped node ignores any weight.
        shaped = {"shape": node.shape} if node.shape is not None else {}
        # A pinned node holds a fixed position with physics off, so a deterministic
        # layout stays put instead of being pulled into a hairball.
        pinned = (
            {"x": node.pin[0], "y": node.pin[1], "fixed": True, "physics": False}
            if node.pin is not None
            else {}
        )
        marked = {"size": SUBJECT_SIZE} if node.id in subjects else {}
        if node.id in heavy:
            marked["mass"] = SUBJECT_MASS
        net.add_node(
            node.id,
            label=node.label,
            title=f"{kind_name(node.kind)}: {node.label}",
            # A grouped node takes its player colour; a plain node the kind colour.
            color=colours[node.id],
            **weighted,
            **shaped,
            **pinned,
            **marked,
        )
        if node.shape is not None:
            # A shaped node draws its label over its own fill rather than over the
            # ground, which is the one place the halo `_SETTLE` sets would outline text
            # in a colour that is not behind it. Only the stroke is turned off: vis.js
            # reads each font key from the most specific source that sets it, so the
            # size still follows the zoom the settle sets on every node.
            #
            # Written after the fact because pyvis's `Node` assigns `font` outright from
            # the network's `font_color`, discarding any font passed as an option.
            net.node_map[node.id]["font"]["strokeWidth"] = 0
    for edge in subgraph.edges:
        # Tint the edge to match its player so a chain reads as one colour; an
        # edge touching the neutral shared event takes the player on its other
        # end. The two players never share an edge (only the neutral event), so
        # source-or-target is unambiguous.
        player = group_by_id.get(edge.source) or group_by_id.get(edge.target)
        # §7: an edge is a hairline on `--text-dim` when neutral, the group's tint in a
        # grouped view, so neither reads as the vis.js default grey slab line. The
        # neutral hairline sat on `--border` until it was measured against the ground it
        # is actually drawn on: `--border` is 1.36:1 on `--surface`, which is a line you
        # infer from the gap between two nodes rather than see. `--text-dim` is 7.80:1,
        # well past the 3:1 WCAG asks of a graphical object that carries meaning, which
        # an edge here does: it is the relation the whole view exists to show.
        tint = {"color": palette[player] if player is not None else TOKENS["text-dim"]}
        # A visible label is drawn on the edge; otherwise it is a hover tooltip.
        text = {"label": edge.label} if edge.visible else {"title": edge.label}
        # A drawn rate is a percent with its base out of sight, and the base is what
        # says how far one deck moves it: the usage tier keeps every archetype slice
        # rather than flooring the thin ones (#211), so a visible rate edge hovers
        # its own counts, in the one sample-size form the charts already use.
        if edge.visible and edge.total_decks is not None:
            text["title"] = count_of(edge.decks, edge.total_decks, "decks")
        net.add_edge(edge.source, edge.target, **text, **tint)

    # A fully pinned graph (the two-seed co-occurrence layout) has nothing for
    # physics to solve, so turn it off: the fixed positions render as-is with no
    # stabilisation jitter.
    all_pinned = bool(subgraph.nodes) and all(n.pin is not None for n in subgraph.nodes)
    if all_pinned:
        net.toggle_physics(False)

    doc = _nothing_from_outside(_hosted_library(net.generate_html(notebook=False)))
    return _compose(doc, all_pinned, [n.id for n in subgraph.nodes if n.id in subjects])


# The on-screen size every node label settles to, in CSS pixels. It matches the colour
# key's chips in the panel beside the graph (`.key-chip`, 12px), so a node's name is
# never smaller than the key it is read beside, and it clears the theme's smallest type
# role (the 11px eyebrow). Nothing about it is a vis.js font size: see `_SETTLE`.
LABEL_PX = 12

# The face every label is drawn in: the app's body face, served by the app
# (`serve.LABEL_FACE_URL`), then a face every browser has. vis.js writes this into a
# canvas font as it stands, where a list of families reads as it does in a stylesheet.
LABEL_FACE = f"'{FONTS['body']['family']}', arial"

# The halo drawn behind every label, in CSS pixels, in the ground's own colour.
# A dense corner of a graph draws its labels across one another, and two names
# in the same ink on the same ground melt into a single unreadable smear; a halo cuts
# each label out of whatever is behind it, so the top one reads and the stack is visible
# as a stack. Three is the width that closes the gaps between glyphs of adjacent labels
# without eating into the letterforms it surrounds.
HALO_PX = 3

# What a selected node and the edges it reaches are drawn with, in CSS pixels: a ring
# round the node, and that much added to each edge's width. vis.js rings a selected
# node in a shade of its own fill and thickens its edges by one canvas unit, which at
# the zoom a hundred nodes are fitted at is under half a pixel on screen: a reader who
# clicked a node, or its row in the panel beside the graph, could not find it (#330).
# Both are drawn in the text colour, the brightest ink the app has, and sized from the
# zoom as the labels are, so they hold on the densest graph drawn: the ring where the
# graph settles (`_SETTLE`), the path wherever a node takes the focus (`_FOCUS`).
RING_PX = 3
PATH_PX = 2.5

# The node a graph was asked about, its subject, stands out from the nodes round it
# (#331): a card's usage graph drew all 106 of its nodes one size, the card among
# them. Its dot is `SUBJECT_SIZE` in vis.js's own units, where every other dot is
# vis.js's default 25, and its name is `SUBJECT_PX` on screen, a step up from
# `LABEL_PX`.
SUBJECT_SIZE = 40
SUBJECT_PX = 15

# The weight a subject is given where the layout pulls it onto another (`_tied`),
# against the 1 of every other node. The two pilots of a head-to-head came to rest 27px apart, one dot over the
# other: every event they share is tied to both, so the layout pulls the pair to one
# point. Where they start makes no difference (measured from 300, 600 and 1200 units
# apart, with and without vis.js's own pre-layout: 23px each time), and a pin would
# take them out of the simulation, which ADR 0018 rules out. A heavier node pushes
# harder on every other, the second subject included, so the two rest apart with
# physics live and each still answers a drag. Measured on a 72-node head-to-head in
# the frame beside the panel: 31px apart at 3, 61px at 12, 120px at 25, and 190px at
# 50, by which point the events have bunched into two clumps. Two cards' co-occurrence
# is fully pinned, so physics is off there and the weight does nothing.
SUBJECT_MASS = 25

# vis.js draws a label in canvas units and multiplies by the zoom, then drops the label
# entirely once that product falls under a floor of its own (`scaling.label.drawThreshold`
# minus one, so 4px). At desktop width the fitted zoom leaves labels small rather than
# absent: measured on a 1440x810 frame, the pilot overview painted at 6.9px and the
# 250-node ceiling (`explore.assess`) at 4.4px, a hair above the drop (#178).
#
# So the font is sized from the zoom rather than left to inherit it. The two are coupled
# in both directions, since a bigger label grows its node's bounding box and so zooms the
# fit back out, and they are settled together here: fit, size the font from the zoom that
# came out, repeat until the zoom stops moving. The loop ends on a fit, so what the reader
# first sees is a fitted graph whose labels are `LABEL_PX` tall.
#
# The fit is ours rather than pyvis's for the same reason: pyvis leaves vis.js's
# `stabilization.fit` on, and that path estimates the zoom from the *node count* instead
# of the drawn extent, which parks the graph inside its frame with ground to spare.
#
# What this settle deliberately never does is the other half of the reverted #174: no
# node is moved and physics is never touched (ADR 0018). It changes font sizes and the
# viewport, both of which physics is indifferent to, so the drag interaction and the
# dynamic edges' support points stay exactly as vis.js solved them.
_SETTLE = """
<script>
  const LABEL_PX = __LABEL_PX__;
  const HALO_PX = __HALO_PX__;
  const LABEL_FACE = "__LABEL_FACE__";
  const RING_PX = __RING_PX__;
  const PINNED = __PINNED__;
  const SUBJECTS = __SUBJECTS__;
  const SUBJECT_PX = __SUBJECT_PX__;
  let hasSettled = false;
  let settledFrame = null;  // the frame the last completed settle fitted, as "WxH"
  let readerHasView = false;  // the reader has zoomed or panned: the view is theirs now

  function framed() {
    // Whether there is a frame to settle against at all. The graph lives in a Gradio
    // tab, so opening another tab hides this one rather than closing it, and a hidden
    // frame reports 0 by 0: fitting to it lands on a garbage zoom, and the label size
    // divides by that zoom.
    const canvas = network.canvas.frame.canvas;
    return canvas.clientWidth > 0 && canvas.clientHeight > 0;
  }

  function settle() {
    // Whatever comes of this, the resize handler takes over afterwards, including when
    // there is no frame yet: a graph left to settle while its tab is hidden is exactly
    // what that handler is there to finish.
    hasSettled = true;
    if (!framed()) return;
    // A settle is owed to a frame, not to an event: coming back to a tab announces the
    // same frame the graph was already fitted to, and refitting then would throw away
    // wherever the reader had panned or zoomed to before they left.
    const canvas = network.canvas.frame.canvas;
    const frame = canvas.clientWidth + "x" + canvas.clientHeight;
    if (frame === settledFrame) return;
    // Three things that each depend on the other two: the zoom a fit lands on, the
    // label size that zoom implies, and the extent the labels give the graph to be
    // fitted. So they are settled together, a pass at a time, until the zoom stops
    // moving. Eight passes is far more than it takes: the widest graph the app will
    // draw, 250 nodes (`explore.assess`), converges to within a twentieth of a pixel
    // of LABEL_PX.
    let zoom = 0;
    for (let pass = 0; pass < 8; pass++) {
      network.fit();
      const fitted = network.getScale();
      const converged = Math.abs(fitted - zoom) < 0.005 * fitted;
      zoom = fitted;
      if (converged) break;
      // The halo rides the same divisor as the label: vis.js measures both in canvas
      // units, so a stroke left at a fixed width thickens as a graph zooms out and
      // swallows the labels it is there to separate.
      network.setOptions({nodes: {font: {
        size: LABEL_PX / zoom, strokeWidth: HALO_PX / zoom,
        strokeColor: '__GROUND__',
      }}});
      // A subject's name is a step larger, and is sized here with the rest because
      // its box is part of what the next pass fits.
      for (const id of SUBJECTS) {
        network.body.nodes[id].setOptions({font: {size: SUBJECT_PX / zoom}});
      }
      // A node's box is measured as it is drawn, and vis.js draws on the next frame,
      // where this loop runs to convergence within one. Without the redraw every pass
      // after the first fits against the box the *previous* label size gave, and the
      // graph settles a little larger than its frame, clipped at the edges.
      network.redraw();
    }
    // A last fit, so the reader always sees a fitted graph: on the pass that converges
    // it is the one already done again, and on a graph that runs the passes out it is
    // the one the loop would otherwise have left undone.
    network.fit();
    // The selection's ring is a width in canvas units too, so it takes the same
    // divisor. Drawn only on what is selected, so it moves no box and no fit.
    network.setOptions({nodes: {borderWidthSelected: RING_PX / network.getScale()}});
    settledFrame = frame;
  }

  // A selected node's ring is drawn in the text colour. vis.js keeps a colour for
  // the selected state on every node, worked out from the one colour it was created
  // with, so it is set on each here and not in the options, which a colour of a
  // node's own outranks. Nothing is added to the data, so the layout is not asked
  // to run again (ADR 0018). The edges a selected node reaches are `_FOCUS`'s.
  for (const node of Object.values(network.body.nodes)) {
    node.options.color.highlight.border = '__INK__';
  }

  // The labels are drawn in the app's face, and a canvas does not wait for a face
  // the way text in a page does: asked for one that is not in yet, it draws in
  // another and never looks again. A label's box is measured at the settle and the
  // fit is solved from those boxes, so the face is asked for by name and the settle
  // waits for it. A face whose request fails settles the graph all the same, in the
  // fallback the stack names.
  const faceIn = document.fonts.load(LABEL_PX + "px " + LABEL_FACE).catch(() => {});
  function settleInTheFace() {
    faceIn.then(() => {
      network.setOptions({nodes: {font: {face: LABEL_FACE}}, edges: {font: {face: LABEL_FACE}}});
      settle();
    });
  }
  // A physics graph is only worth fitting once its layout has stopped moving; a graph
  // whose every node is pinned never stabilises, because there is nothing to solve.
  if (PINNED) { settleInTheFace(); }
  else { network.once("stabilizationIterationsDone", settleInTheFace); }
  // The settle owns the view only until the reader takes it: a wheel or button zoom
  // (vis emits "zoom" for those and never for our own fits) or a pan (a drag holding
  // no node) makes the view theirs, and no frame change refits it out from under them
  // after that: a window dragged narrower leaves them where they were reading.
  network.on("zoom", () => { readerHasView = true; });
  network.on("dragEnd", (drag) => { if (drag.nodes.length === 0) readerHasView = true; });
  // A frame that changes size is a different frame to fit, so a resized window or a
  // tab coming back into view settles again. Held back until the first settle has run,
  // since vis.js reports a size on the way up too and a fit taken mid-stabilisation
  // would size the labels from the scatter rather than the layout. Deferred rather
  // than settled in the handler, for two reasons: vis.js announces a resize *before*
  // rescaling its own view to the new width, so a fit done in the handler is then
  // multiplied away; and a dragged window edge is a stream of resizes, each of which
  // would pay a full multi-pass settle over every node, so only the last one counts.
  let resettle = null;
  network.on("resize", () => {
    if (!hasSettled) return;
    clearTimeout(resettle);
    resettle = setTimeout(() => { if (!readerHasView) settle(); }, 150);
  });
</script>
"""

# How far a node, a name or an edge outside the neighbourhood in focus is taken toward
# the ground, as a share of the way there. At 0.82 the eight palette colours land
# between 1.17:1 and 1.30:1 on the ground, and a neutral edge at 1.40:1: there to say
# where the rest of the graph is and no more. A name is taken less far, to 2.33:1,
# since a thin stroke fades faster than a filled dot.
DIM = 0.82
DIM_NAME = 0.72

# On a dense graph a selected node's ring and bright edges were lit among a hundred
# nodes drawn as bright as they were (#331). A node in focus, the one under the
# pointer or else the selected one, brings its neighbourhood forward: every node it
# does not reach, and every edge that does not touch it, is taken most of the way to
# the ground, and each edge it reaches is drawn in the text colour, `PATH_PX` wider,
# with its quantity at the size of a name.
# Nothing changes at rest, and losing the focus puts every colour back.
#
# The focus is read as the graph is drawn, off what is hovered and selected at that
# moment, and not from the events that change either: a selection made from the panel
# beside the frame (`_ANSWER`) raises no event of vis.js's own, and it draws all the
# same. What is drawn is repainted only when the focus changes, or the zoom under a
# focus, since the widths are sizes on screen. An edge selected on its own, with no
# node, is lit as a reached edge is and dims nothing.
#
# It writes colours and widths onto the nodes and edges vis.js already holds, and adds
# nothing to the data, so the layout is not asked to run again: no node is moved and
# physics is never touched (ADR 0018). It runs after pyvis's ``drawGraph()`` has
# assigned the global ``network``, and after `_SETTLE`, whose `SUBJECTS`, `LABEL_PX`
# and `HALO_PX` it reads. It is inside a function so that it adds no name of its own
# to the ones the document's scripts share.
_FOCUS = """
<script>
  (() => {
    const INK = '__INK__', GROUND = '__GROUND__', CLEAR = 'rgba(0,0,0,0)';
    const PATH_PX = __PATH_PX__;
    const body = network.body;
    const toGround = (hex, share) => '#' + [1, 3, 5].map((at) => Math.round(
      parseInt(hex.slice(at, at + 2), 16) * (1 - share)
      + parseInt(GROUND.slice(at, at + 2), 16) * share
    ).toString(16).padStart(2, '0')).join('');

    // What each node and edge is drawn with at rest, kept so it can be put back.
    const nodeRest = {}, edgeRest = {};
    for (const [id, node] of Object.entries(body.nodes)) {
      nodeRest[id] = {fill: node.options.color.background, border: node.options.color.border};
    }
    for (const [id, edge] of Object.entries(body.edges)) {
      const {color, strokeColor, size, strokeWidth} = edge.labelModule.fontOptions;
      edgeRest[id] = {colour: edge.options.color.color, width: edge.options.width,
                      quantity: {color, strokeColor, size, strokeWidth}};
    }
    const DIM_INK = toGround(INK, __DIM_NAME__);
    // vis.js draws in the order it holds its nodes and edges in, later over earlier.
    const nodeOrder = [...body.nodeIndices], edgeOrder = [...body.edgeIndices];
    const subjects = new Set(SUBJECTS);
    const last = (order, those) => [...order.filter((id) => !those.has(id)),
                                    ...order.filter((id) => those.has(id))];

    function ink(label, colour, halo) {
      label.fontOptions.color = colour;
      label.fontOptions.strokeColor = halo;
      label.labelDirty = true;
    }

    function paint(focus, lit) {
      const scale = network.getScale();
      const near = focus === null ? null : new Set([focus, ...network.getConnectedNodes(focus)]);
      for (const [id, rest] of Object.entries(nodeRest)) {
        const node = body.nodes[id], out = near !== null && !near.has(id);
        // A subject's dot stays lit wherever the focus is: it is what the graph is about.
        const dim = out && !subjects.has(id);
        const fill = dim ? toGround(rest.fill, __DIM__) : rest.fill;
        const border = dim ? toGround(rest.border, __DIM__) : rest.border;
        // The same under the pointer as away from it: a hovered node's answer is its
        // neighbourhood, and vis.js goes on holding a node as hovered after the
        // pointer has left the canvas over it.
        Object.assign(node.options.color, {background: fill, border, hover: {background: fill, border}});
        // A halo parts two names drawn over one another. A dimmed name is given
        // none: its halo would be cut out of the lit nodes it lies across.
        ink(node.labelModule, out ? DIM_INK : INK, out ? CLEAR : GROUND);
      }
      for (const [id, rest] of Object.entries(edgeRest)) {
        const edge = body.edges[id], reached = lit.has(id);
        const colour = reached ? INK : (focus === null ? rest.colour : toGround(rest.colour, __DIM__));
        // One colour for every state vis.js keeps, so an edge is drawn by the focus
        // alone, the same whether its node is under the pointer or selected.
        Object.assign(edge.options.color, {color: colour, highlight: colour, hover: colour});
        edge.options.width = reached ? rest.width + PATH_PX / scale : rest.width;
        if (!edge.options.label) continue;
        // A quantity is drawn at vis.js's own size scaled by the zoom (ADR 0018),
        // too small to read on a fitted graph. A reached edge states its own as a
        // name is stated, and the others are not drawn while there is a focus.
        Object.assign(edge.labelModule.fontOptions, reached
          ? {color: INK, strokeColor: GROUND,
             size: LABEL_PX / scale, strokeWidth: HALO_PX / scale}
          : (focus === null ? rest.quantity
                            : {...rest.quantity, color: CLEAR, strokeColor: CLEAR}));
        edge.labelModule.labelDirty = true;
      }
      // The neighbourhood is drawn over what it is not, so nothing dimmed cuts across
      // it, and a subject over whatever the layout has left lying across it. vis.js
      // itself draws a selected node, then a hovered one, after all of these.
      body.nodeIndices.splice(0, Infinity,
        ...last(near === null ? nodeOrder : last(nodeOrder, near), subjects));
      body.edgeIndices.splice(0, Infinity, ...last(edgeOrder, lit));
    }

    // The pointer over a node is a focus, so the widget is asked to say when it is.
    // The widths it would add to a hovered or selected edge are this script's to add.
    network.setOptions({interaction: {hover: true},
                        edges: {selectionWidth: 0, hoverWidth: 0}});
    // Nothing is painted yet, so the first draw paints the graph at rest and puts
    // its subjects last.
    let hovered = null, painted = null;
    network.on('hoverNode', (params) => { hovered = params.node; });
    network.on('blurNode', () => { hovered = null; });
    // vis.js is not told when the pointer leaves the canvas, so the last node it
    // crossed on its way to the panel would keep the focus from a row clicked there.
    network.canvas.frame.addEventListener('mouseleave', () => {
      hovered = null;
      network.redraw();
    });
    network.on('beforeDrawing', () => {
      const selected = network.getSelectedNodes();
      const focus = hovered !== null ? hovered : (selected.length ? selected[0] : null);
      // The edges drawn lit: the ones the focus reaches, or with no focus an edge the
      // reader has selected on its own, which dims nothing.
      const lit = focus === null ? network.getSelectedEdges() : network.getConnectedEdges(focus);
      const state = lit.length ? [focus, network.getScale(), ...lit].join('|') : '';
      if (state === painted) return;
      painted = state;
      paint(focus, new Set(lit));
    });
  })();
</script>
"""

# The iframe is an isolated document, so the parent's `:root` tokens do not reach
# it: the theme is carried in as literal token values here, and the face as a rule of
# its own naming the copy the app serves. It lays the doc out as a full-height flex
# column the graph fills, so the height is whatever the embedding iframe gives it. It
# also retires pyvis/bootstrap's own light chrome: the white `.card`, the `lightgray`
# graph border, and the empty centred heading.
#
# Bootstrap itself is not loaded (`_nothing_from_outside`). The layout rested on its
# rules, two of which are stated here in its place: every box sized to its border,
# and a line height of 1.5 (issue #312). A third was the 16px `.card-body` put round
# the graph, which is not carried since #330: a fit already leaves a margin of its
# own round the drawing, and beside a panel the frame is a third narrower, so those
# 32px each way are what keeps the drawing as large as it was at full width.
# `text-size-adjust` is carried unmeasured: it stops a phone's browser resizing the
# text when the phone is turned, which a desktop browser cannot show.
_DOC_STYLE = f"""<style>
  @font-face {{
    font-family: '{FONTS["body"]["family"]}'; font-style: normal;
    font-weight: {FONTS["body"]["weight"]}; font-display: swap;
    src: url({LABEL_FACE_URL}) format('woff2');
  }}
  *, ::before, ::after {{ box-sizing: border-box; }}
  html, body {{ height: 100%; margin: 0; }}
  body {{
    display: flex; flex-direction: column; line-height: 1.5;
    -webkit-text-size-adjust: 100%; text-size-adjust: 100%;
    background: {TOKENS['surface']}; color: {TOKENS['text']}; font-family: {FONT_STACK};
  }}
  center {{ display: none; }}
  .card {{
    flex: 1 1 auto; display: flex; min-height: 0;
    margin: 0; border: none !important; background: transparent !important;
  }}
  #mynetwork {{
    flex: 1 1 auto; height: auto !important; min-height: 0; padding: 0;
    float: none !important; border: none !important; background: {TOKENS['surface']} !important;
  }}
  .vis-tooltip {{
    background: {TOKENS['surface-2']} !important; color: {TOKENS['text']} !important;
    border: 1px solid {TOKENS['border']} !important; border-radius: 4px !important;
    font-family: {FONT_STACK} !important; font-size: 12px !important; padding: 4px 8px !important;
  }}
</style>"""

# What a node click is answered with is the page's: a panel beside this frame
# (`parts.beside_graph`), holding a block of facts for every node drawn and, under
# them, the page's list. This script is the wire between the two. A click on a node
# shows that node's block; a click on a row of the list, or on a neighbour named in a
# block, selects that node in the graph and shows its block in turn.
#
# The frame is set from `srcdoc`, so it shares the page's origin and may reach the
# panel through its own frame element. A document drawn alone, as the browser tests
# draw it, has no frame and no panel, and the script then does nothing.
#
# The names are matched by comparing strings and never built into a selector, since a
# node id is free text from the source (a card's or an event's name). It runs after
# pyvis's own ``drawGraph()`` has assigned the global ``network``, inside a function
# so that it adds no name to the ones the document's scripts already share.
_ANSWER = """
<script>
  (() => {
    const frame = window.frameElement;
    const side = frame && frame.closest(".graph-split")
      && frame.closest(".graph-split").querySelector(".graph-panel");
    if (!side) return;
    function answer(id) {
      let answered = null;
      for (const facts of side.querySelectorAll(".node-facts")) {
        facts.hidden = facts.dataset.node !== id;
        if (!facts.hidden) answered = facts;
      }
      for (const prompt of side.querySelectorAll(".panel-prompt")) {
        prompt.hidden = answered !== null;
      }
      for (const row of side.querySelectorAll("tr[data-node]")) {
        row.classList.toggle("picked", row.dataset.node === id);
      }
      return answered;
    }
    network.on("selectNode", (params) => {
      // The reader is looking at the graph, so the answer is brought to them: back
      // up a panel they have scrolled down its list, and into the window where the
      // page has the top of the panel off screen. By the least that shows it, which
      // is nothing at all when it is already in view.
      const answered = answer(params.nodes[0]);
      if (answered) answered.scrollIntoView({block: "nearest"});
    });
    network.on("deselectNode", () => answer(null));
    side.addEventListener("click", (event) => {
      if (event.target.closest("a")) return;
      const named = event.target.closest("[data-node]:not(.node-facts)");
      if (!named || !network.body.nodes[named.dataset.node]) return;
      network.selectNodes([named.dataset.node]);
      answer(named.dataset.node);
    });
  })();
</script>
"""


def _compose(doc: str, all_pinned: bool, subjects: list[str]) -> str:
    """Bring the standalone pyvis document onto the theme: inject the dark doc-level
    style, and at the end of the body the fit-and-label settle, the focus a hovered
    or selected node takes, and the script that answers a click in the panel beside
    the frame."""
    settle = (
        _SETTLE.replace("__LABEL_PX__", str(LABEL_PX))
        .replace("__HALO_PX__", str(HALO_PX))
        .replace("__LABEL_FACE__", LABEL_FACE)
        .replace("__RING_PX__", str(RING_PX))
        .replace("__INK__", TOKENS["text"])
        .replace("__GROUND__", TOKENS["surface"])
        .replace("__PINNED__", "true" if all_pinned else "false")
        .replace("__SUBJECT_PX__", str(SUBJECT_PX))
        .replace("__SUBJECTS__", json.dumps(subjects))
    )
    focus = (
        _FOCUS.replace("__INK__", TOKENS["text"]).replace("__GROUND__", TOKENS["surface"])
        .replace("__DIM_NAME__", str(DIM_NAME)).replace("__DIM__", str(DIM))
        .replace("__PATH_PX__", str(PATH_PX))
    )
    doc = _inject(doc, "</head>", _DOC_STYLE + "</head>")
    return _inject(doc, "</body>", settle + focus + _ANSWER + "</body>")


def _inject(doc: str, anchor: str, replacement: str) -> str:
    """Replace ``anchor``'s first occurrence, failing loudly if it is absent.

    Unlike a bare ``str.replace``, a missing anchor is an error rather than a silent
    no-op: pyvis's template shifting (an attributed ``<body ...>``, a renamed tag)
    would otherwise drop the theme and draw the graph on pyvis's white chrome with
    no signal, the same failure :func:`_hosted_library` guards for the library tags.
    """
    if anchor not in doc:
        raise RuntimeError(
            f"pyvis emitted no {anchor!r}: its template has changed, and the graph "
            "would draw un-themed."
        )
    return doc.replace(anchor, replacement, 1)
