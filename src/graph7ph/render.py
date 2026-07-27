"""Render a Subgraph to an interactive pyvis widget (HTML string).

The widget names the vis.js library the app serves itself rather than carrying a
copy, so a result is a few KB and the library is downloaded once (issue #97).
The document lays out as a colour key, the graph filling the middle, and a details
panel below it; clicking a node fills that panel, and a deck's details link out to
its Moxfield page.

pyvis's own generated graph internals (its node/edge JS, its layout) are deliberately
not asserted, per the PRD testing plan. What this module *injects onto* that document
is: the tests hold the shared-palette node colours, the on-screen colour key, the dark
ground, and the structured details panel, since those are this module's contribution
and the visual-direction contract (§7) they answer to.

One part of that contribution cannot be held in the emitted string at all. Whether a
label reaches the reader is decided at paint time, out of the zoom a fit lands on and
the frame it lands in (see :data:`_SETTLE`), and #161 is what happens when nobody is
watching those numbers: 67 nodes drawn as bare dots at phone width, with the HTML
saying nothing about it. ``tests/test_graph_phone.py`` draws these documents on a real
browser at 390 CSS pixels and measures them there.
"""

import html
import json
import re

from pyvis.network import Network

from graph7ph.palette import CATEGORICAL, assign
from graph7ph.query import Node, Subgraph
from graph7ph.serve import VIS_CSS_URL, VIS_JS_URL
from graph7ph.theme import FONT_STACK, TOKENS

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

# Grouped views colour by group instead of by kind, so the groups read apart at
# a glance: a head-to-head tints each player's chain, and card co-occurrence
# tints each seed card and its shared partners. Slots 1-3 of the shared set, the
# trio that stays distinct under adjacency (§7); up to three groups present (two
# seeds and their partners).
_GROUP_COLOURS = CATEGORICAL[:3]

# A deck's stable id is its Moxfield public id, so its authoritative list is a
# direct construction (confirmed against the source's own ``url`` field).
_MOXFIELD = "https://moxfield.com/decks/{}"


def _moxfield_url(node: Node) -> str | None:
    """The Moxfield page for a deck node, or ``None`` for any other kind."""
    if node.kind != "Deck":
        return None
    return _MOXFIELD.format(node.id.removeprefix("deck:"))


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


def render_subgraph(subgraph: Subgraph) -> str:
    # Both dimensions are the frame's, not a number of our own: `_DOC_STYLE` lays the
    # document out as a full-height flex column and the graph fills what is left, so
    # the height the embedding iframe sets (`app._embed`, responsive) is the height.
    net = Network(
        height="100%", width="100%", directed=True, cdn_resources="remote",
        bgcolor=TOKENS["surface"], font_color=TOKENS["text"],
    )
    # A stable colour per player group present, so the two chains stay distinct.
    groups = sorted({n.group for n in subgraph.nodes if n.group is not None})
    palette = {g: _GROUP_COLOURS[i % len(_GROUP_COLOURS)] for i, g in enumerate(groups)}
    group_by_id = {n.id: n.group for n in subgraph.nodes}
    for node in subgraph.nodes:
        # A weighted node is sized by its value; vis.js scales the values in the
        # graph between a min and max radius, so bigger weight reads as a bigger
        # node. Unweighted nodes render at the default size.
        weighted = {"value": node.weight} if node.weight is not None else {}
        # A grouped node takes its player colour; a plain node the kind colour.
        colour = palette[node.group] if node.group is not None else _KIND_COLOURS.get(node.kind)
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
        net.add_node(
            node.id,
            label=node.label,
            title=f"{node.kind}: {node.label}",
            color=colour,
            **weighted,
            **shaped,
            **pinned,
        )
    for edge in subgraph.edges:
        # Tint the edge to match its player so a chain reads as one colour; an
        # edge touching the neutral shared event takes the player on its other
        # end. The two players never share an edge (only the neutral event), so
        # source-or-target is unambiguous.
        player = group_by_id.get(edge.source) or group_by_id.get(edge.target)
        # §7: an edge is a hairline on `--border` when neutral, the group's tint
        # in a grouped view, so neither reads as the vis.js default grey slab line.
        tint = {"color": palette[player] if player is not None else TOKENS["border"]}
        # A visible label is drawn on the edge; otherwise it is a hover tooltip.
        text = {"label": edge.label} if edge.visible else {"title": edge.label}
        net.add_edge(edge.source, edge.target, **text, **tint)

    # A fully pinned graph (the two-seed co-occurrence layout) has nothing for
    # physics to solve, so turn it off: the fixed positions render as-is with no
    # stabilisation jitter.
    all_pinned = bool(subgraph.nodes) and all(n.pin is not None for n in subgraph.nodes)
    if all_pinned:
        net.toggle_physics(False)

    meta = {
        node.id: {
            "kind": node.kind,
            "label": node.label,
            "moxfield": _moxfield_url(node),
        }
        for node in subgraph.nodes
    }
    # The colour key names what colour actually encodes in this render: the groups
    # in a grouped view, otherwise the kinds present, each beside its swatch. Only
    # what is actually drawn is keyed, in the fixed slot order.
    if groups:
        legend = [
            (name, palette[g])
            for g in groups
            if (name := _group_name(g, subgraph.nodes))
        ]
    else:
        kinds_present = {n.kind for n in subgraph.nodes}
        legend = [(k, _KIND_COLOURS[k]) for k in _KIND_ORDER if k in kinds_present]
    doc = _hosted_library(net.generate_html(notebook=False))
    return _compose(doc, meta, _legend(legend), all_pinned)


_PROMPT = "Click a node to see its details."

# Shipped hidden on every graph and revealed by the settle only where the view's names
# are too wide to draw at the frame's width (see `_SETTLE`), so the fallback is a
# sentence on screen rather than a graph of unexplained bare dots (#161).
_TOO_NARROW = (
    '<p id="too-narrow" hidden>Names here are too long to draw at this width, '
    "so click a node for its details.</p>"
)

# The on-screen size every node label is drawn at, in CSS pixels. It matches the colour
# key's chips (`.legend-chip`, 12px) in the same document, so a node's name is never
# smaller than the key it is read beside, and it clears the theme's smallest type role
# (the 11px eyebrow). Nothing about it is a vis.js font size: see `_SETTLE`.
LABEL_PX = 12

# vis.js draws a label in canvas units and multiplies by the zoom, then drops the label
# entirely once that product falls under a floor of its own (`scaling.label.drawThreshold`
# minus one, so 4px). A graph that fits itself into a phone-width frame zooms to about
# 0.23, which took the default 14px label to 3.3px and suppressed every one of them:
# issue #161, where a 67-node neighbourhood drew as 67 bare coloured dots.
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
_SETTLE = """
<script>
  const LABEL_PX = __LABEL_PX__;
  const PINNED = __PINNED__;
  let stretchedBy = 1;  // how far `fillFrame` has pulled the layout from its own shape
  let origin = null;    // the layout as it was solved, before any of that pulling
  let hasSettled = false;
  let settling = false;

  function framed() {
    // Whether there is a frame to settle against at all. Every tab's graph stays in the
    // document, so opening another tab hides this one rather than closing it, and a
    // hidden frame reports 0 by 0.
    const canvas = network.canvas.frame.canvas;
    return canvas.clientWidth > 0 && canvas.clientHeight > 0;
  }

  function setLabelSizes(nodeSize, edgeSize) {
    // A node's size goes through its own data rather than the graph's options, because
    // vis.js restores an unweighted node's font to the size it was created at whenever
    // *any* node in the graph carries a weight. Set on the graph, the size would reach
    // every weighted node and silently miss the rest: on the archetype affinity view
    // that is the pilot the whole graph hangs off, left at 7px among 12px archetypes.
    // Written into the node, it is the size restored.
    const nodes = network.body.data.nodes;
    nodes.update(nodes.getIds().map((id) => ({id, font: {size: nodeSize}})));
    network.setOptions({edges: {font: {size: edgeSize}}});
  }

  function drawnBox() {
    // What the reader sees the graph occupy, in canvas units: the nodes *and* their
    // labels, which is the extent vis.js itself fits against. Read after a fit, which
    // is what recomputes a node's box against the current label size.
    const box = {left: Infinity, right: -Infinity, top: Infinity, bottom: -Infinity};
    for (const id of network.body.nodeIndices) {
      const bounds = network.body.nodes[id].shape.boundingBox;
      box.left = Math.min(box.left, bounds.left);
      box.right = Math.max(box.right, bounds.right);
      box.top = Math.min(box.top, bounds.top);
      box.bottom = Math.max(box.bottom, bounds.bottom);
    }
    return box;
  }

  function fillFrame() {
    // A fit keeps the layout's own proportions, so a round graph in a portrait frame can
    // only ever fill the narrower axis: #161's neighbourhood used 67 percent of its
    // frame's height, with empty ground above and below. The layout is stretched onto the
    // frame's shape so both axes are used.
    //
    // There is no meaning in that geometry to distort. Every edge asks physics for the
    // same spring length, so what a layout says is which nodes are joined, not how far
    // apart they landed, and a composed layout (co-occurrence) is stretched along one
    // axis only, which leaves its columns and their order exactly as composed. A graph
    // that is genuinely a line *is* a shape of its own, so the stretch is capped at how
    // far out of square the frame is: a four-node chain stays a chain rather than being
    // fanned out to fill the ground.
    const canvas = network.canvas.frame.canvas;
    const box = drawnBox();
    const wide = box.right - box.left, tall = box.bottom - box.top;
    if (!(wide > 0) || !(tall > 0)) return;
    const frame = canvas.clientHeight / canvas.clientWidth;
    const want = frame / (tall / wide);
    const axis = want > 1 ? "y" : "x";  // the one with ground to spare
    // The cap is a budget over the whole settle, not a limit per pass: the passes
    // compound, so a per-pass limit would pull a line into a frame-shaped cloud a
    // stretch at a time.
    const budget = Math.max(frame, 1 / frame) / stretchedBy;
    if (budget <= 1) return;
    const grow = Math.min(Math.max(want, 1 / want), budget);
    stretchedBy *= grow;
    const middle = axis === "y" ? (box.top + box.bottom) / 2 : (box.left + box.right) / 2;
    // Physics off first, or it would pull every moved node straight back. The layout has
    // stabilised by now, so nothing is left for it to solve; what this gives up is a
    // dragged node's neighbours following it, which on a settled graph is jitter.
    network.setOptions({physics: {enabled: false}});
    const at = network.getPositions();
    for (const id of Object.keys(at)) {
      const moved = middle + (at[id][axis] - middle) * grow;
      network.moveNode(id, axis === "x" ? moved : at[id].x, axis === "y" ? moved : at[id].y);
    }
  }

  function tooNarrowToName() {
    // "The name beside the node" needs somewhere beside the node to put it. Some views
    // name a node with a whole deck title ("051st Ben F - Academy Shops - WAC"), which
    // at a phone's width is most of the frame for one name: measured across the views,
    // the typical label is a tenth to a fifth of the frame, and the hidden gems view's
    // is three fifths. Names that wide cannot go side by side, so 46 of them are drawn
    // over each other and leave the graph less readable than the bare dots #161 started
    // from. Half the frame is the line, since past it no two names can sit abreast.
    //
    // The typical label decides it, not the widest: one long name among short ones is a
    // label that overlaps, which every crowded graph has, not a view that cannot be
    // labelled at all.
    //
    // Two more direct-looking measures were tried and dropped, so do not reach for them
    // again. Counting the labels that overlap another says exactly the right thing and
    // cannot be relied on: the physics layout is not deterministic, and the same view
    // measured 43, 45, 54 and 57 percent over four runs, against the 100 percent gems
    // draws, so any threshold between them decides a view's fallback by luck. Comparing
    // the ink the labels want against the canvas would be a proof rather than a
    // threshold, but it never fires: gems asks for 0.64 of the canvas, so the labels
    // *could* be packed in and only a force layout stops them. Label width against frame
    // width is the one that holds still, because it depends on the text and the frame
    // and not on where physics dropped anything.
    //
    // Truncating instead was considered and does not answer it: what distinguishes one
    // of these titles from the next is at its end (the event), so a title cut to fit
    // half the frame leaves every node reading "051st Ben F - Acad".
    const canvas = network.canvas.frame.canvas;
    const zoom = network.getScale();
    const widths = network.body.nodeIndices
      .map((id) => network.body.nodes[id].labelModule.size.width * zoom / canvas.clientWidth)
      .sort((a, b) => a - b);
    return widths.length > 0 && widths[Math.floor(widths.length / 2)] > 0.5;
  }

  function sayTooNarrow() {
    // The fallback is stated rather than left to be inferred from a graph of bare dots,
    // and it points at the answer that does fit: the details panel, which names one node
    // at a time. The names go off rather than shrink, since a name too small to read is
    // the state #161 was filed about. The quantities on the edges stay, sized from the
    // zoom the graph refits to without the names: they are short enough to fit anywhere
    // (a rate is "62%"), and it is only the names that were too wide.
    document.getElementById("too-narrow").hidden = false;
    setLabelSizes(0, 0);
    network.redraw();
    network.fit();
    setLabelSizes(0, LABEL_PX / network.getScale());
    network.redraw();
  }

  function settle() {
    // Whatever comes of this, the resize handler takes over afterwards, including when
    // there is no frame yet: a graph left to settle while its tab is hidden is exactly
    // what that handler is there to finish.
    hasSettled = true;
    if (!framed()) return;
    // vis.js resizes its canvas at the end of every `setOptions` and announces it there
    // and then, and this settle changes the frame itself: the fallback notice is a row in
    // the same flex column as the canvas, so showing it takes ~34px off the graph and
    // hiding it gives them back. Those are this settle's own doing and it has already
    // fitted around them. Left to start another settle, the two take turns, each undoing
    // the last one's notice, eight fits at a time.
    if (settling) return;
    settling = true;
    try {
      // Every settle stretches the layout that was solved, never one the last settle has
      // already stretched. Otherwise the cap is spent again on each resize, and the
      // promise that a graph which is a line stays one holds only until the first
      // rotation: a chain would be fanned out a little further every time.
      if (origin === null) {
        origin = network.getPositions();
      } else {
        network.setOptions({physics: {enabled: false}});
        for (const id of Object.keys(origin)) {
          network.moveNode(id, origin[id].x, origin[id].y);
        }
      }
      stretchedBy = 1;
      document.getElementById("too-narrow").hidden = true;
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
        fillFrame();
        // Sized for both, since the signed rule puts the name beside the node and the
        // quantity on the edge, and vis.js was dropping each for the same reason.
        setLabelSizes(LABEL_PX / zoom, LABEL_PX / zoom);
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
      if (tooNarrowToName()) sayTooNarrow();
    } finally {
      settling = false;
    }
  }

  // A physics graph is only worth fitting once its layout has stopped moving; a graph
  // whose every node is pinned never stabilises, because there is nothing to solve.
  if (PINNED) { settle(); } else { network.once("stabilizationIterationsDone", settle); }
  // A frame that changes size is a different frame to fill and a different width to fit a
  // name into, so a rotated phone, a dragged window, or a tab coming back into view
  // settles again. Held back until the first settle has run, since vis.js reports a size
  // on the way up too and settling then would turn physics off (see `fillFrame`) partway
  // through stabilising.
  network.on("resize", () => { if (hasSettled) settle(); });
</script>
"""

# The iframe is an isolated document, so the parent's `:root` tokens do not reach
# it: the theme is carried in as literal token values here. It lays the doc out as
# a flex column (key on top, graph filling the middle, details at the bottom), so
# the height is whatever the embedding iframe gives it and the details stay visible
# without scrolling. It also retires pyvis/bootstrap's own light chrome: the white
# `.card`, the `lightgray` graph border, and the empty centred heading.
_DOC_STYLE = f"""<style>
  html, body {{ height: 100%; margin: 0; }}
  body {{
    display: flex; flex-direction: column;
    background: {TOKENS['surface']}; color: {TOKENS['text']}; font-family: {FONT_STACK};
  }}
  center {{ display: none; }}
  .card {{
    flex: 1 1 auto; display: flex; min-height: 0;
    margin: 0; border: none !important; background: transparent !important;
  }}
  #mynetwork {{
    flex: 1 1 auto; height: auto !important; min-height: 0;
    float: none !important; border: none !important; background: {TOKENS['surface']} !important;
  }}
  /* A neutral hairline under the key, ruling it off from the canvas below. An accent
     box round it was tried and dropped at the maintainer's call (2026-07-26), the same
     call that took the box off the chart legends, so do not add one back. */
  #graph-legend {{
    flex: 0 0 auto; display: flex; flex-wrap: wrap; gap: 0.85rem;
    padding: 0.55rem 0.85rem; border-bottom: 1px solid {TOKENS['border']};
  }}
  .legend-chip {{
    display: inline-flex; align-items: center; gap: 0.4rem;
    font-size: 12px; color: {TOKENS['text-dim']};
  }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  /* The stated fallback when a view's names are too wide for the frame (#161). Sits
     under the key, in the key's own caption size and ink, and says nothing at all on
     the views that do fit their names. */
  #too-narrow {{
    flex: 0 0 auto; margin: 0; padding: 0.55rem 0.85rem;
    border-bottom: 1px solid {TOKENS['border']};
    font-size: 12px; color: {TOKENS['text-dim']};
  }}
  .vis-tooltip {{
    background: {TOKENS['surface-2']} !important; color: {TOKENS['text']} !important;
    border: 1px solid {TOKENS['border']} !important; border-radius: 4px !important;
    font-family: {FONT_STACK} !important; font-size: 12px !important; padding: 4px 8px !important;
  }}
  #node-details {{
    flex: 0 0 auto; padding: 0.7rem 0.85rem;
    border-top: 1px solid {TOKENS['border']}; font-size: 14px;
  }}
  .nd-row {{ display: flex; gap: 0.85rem; padding: 0.12rem 0; }}
  .nd-label {{
    flex: 0 0 3.25rem; color: {TOKENS['text-mute']};
    text-transform: uppercase; font-size: 11px; letter-spacing: 0.06em; line-height: 1.6;
  }}
  .nd-value {{ color: {TOKENS['text']}; }}
  .nd-prompt {{ margin: 0; color: {TOKENS['text-mute']}; }}
  .nd-link {{ color: {TOKENS['accent-bright']}; text-decoration: none; }}
  .nd-link:hover {{ text-decoration: underline; }}
</style>"""

# A details panel keyed on the vis node id: on click it shows the selected node's
# kind and name as labelled field rows, and for a deck its Moxfield page as a link
# affordance. The script runs after pyvis's own ``drawGraph()`` has assigned the
# global ``network``. The label is untrusted (a display name recovered from a
# Moxfield deck title), so it is written through textContent and a built <a>
# element, never innerHTML.
_PANEL = """
<div id="node-details"><p class="nd-prompt">__PROMPT__</p></div>
<script>
  const NODE_META = __META__;
  const panel = document.getElementById("node-details");
  function row(labelText, value) {
    const r = document.createElement("div"); r.className = "nd-row";
    const k = document.createElement("span"); k.className = "nd-label"; k.textContent = labelText;
    const v = document.createElement("span"); v.className = "nd-value";
    if (typeof value === "string") { v.textContent = value; } else { v.appendChild(value); }
    r.appendChild(k); r.appendChild(v); return r;
  }
  function showNode(id) {
    const m = NODE_META[id];
    if (!m) return;
    panel.replaceChildren(row("Kind", m.kind), row("Name", m.label));
    if (m.moxfield) {
      const a = document.createElement("a");
      a.href = m.moxfield;
      a.target = "_blank";
      a.rel = "noopener";
      a.className = "nd-link";
      a.textContent = "Open on Moxfield \\u2197";
      panel.appendChild(row("Deck", a));
    }
  }
  function reset() {
    const p = document.createElement("p"); p.className = "nd-prompt"; p.textContent = "__PROMPT__";
    panel.replaceChildren(p);
  }
  network.on("selectNode", (params) => showNode(params.nodes[0]));
  network.on("deselectNode", reset);
</script>
"""


def _legend(pairs: list[tuple[str, str]]) -> str:
    """The on-screen colour key: one chip per drawn kind or group, a swatch beside
    its name, so identity is carried by the label and not by hue alone (§7).

    Labels can be untrusted (a group is a player or card name), so each is escaped;
    the swatch colour is one of our own palette hexes.
    """
    if not pairs:
        return ""
    chips = "".join(
        f'<span class="legend-chip">'
        f'<span class="legend-dot" style="background:{colour}"></span>'
        f"{html.escape(label)}</span>"
        for label, colour in pairs
    )
    return f'<div id="graph-legend">{chips}</div>'


def _compose(doc: str, meta: dict, legend: str, all_pinned: bool) -> str:
    """Bring the standalone pyvis document onto the theme: inject the dark doc-level
    style, the colour key at the top of the body, and the details panel and the
    fit-and-label settle at the end.

    The node metadata is embedded in an inline ``<script>``, so ``<``/``>``/``&``
    are unicode-escaped to stop a label ever closing the tag (``</script>``).
    """
    payload = (
        json.dumps(meta)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    panel = _PANEL.replace("__META__", payload).replace("__PROMPT__", _PROMPT)
    settle = (
        _SETTLE.replace("__LABEL_PX__", str(LABEL_PX))
        .replace("__PINNED__", "true" if all_pinned else "false")
    )
    doc = _inject(doc, "</head>", _DOC_STYLE + "</head>")
    doc = _inject(doc, "<body>", "<body>" + legend + _TOO_NARROW)
    return _inject(doc, "</body>", panel + settle + "</body>")


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
