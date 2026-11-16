"""PROTOTYPE for issue #331. Throwaway: no tests, never merged into main.

The question: should the graph mark the card or pilot it was asked about, should a
hovered or selected node bring its neighbourhood forward, and are fewer labels at
rest worth it? Three variants on the real app and the real data, switched by
``?variant=`` and by the pill at the foot of the page (arrow keys work too):

  a  Today             the graph as it ships
  b  Subject + focus   the subject is a larger dot with a larger name, the two pilots
                       of a head-to-head sit apart, and a hovered or selected node
                       dims everything outside its neighbourhood
  c  Hub labels        b, and at rest only the subject, what it reaches and the hubs
                       are named; the rest on focus or zoom, edge quantities on focus

Run from the worktree, against the main checkout's built graph::

    GRADIO_SERVER_PORT=7891 uv run python -m graph7ph.prototype_331

Everything here is bolted onto the shipped modules from outside (module attributes
swapped at start-up, the graph document patched as a string), so no production file
is edited. A switch of variant redraws every graph on the page, so each is laid out
from scratch the way it would ship.

Measured while building it (Ariel M against Adam D, 72 nodes, a 791 by 702 frame):
a starting position does not keep two pilots apart. Started 300, 600 or 1200 units
apart, with or without vis.js's own pre-layout, they settle 23px apart, as they do
today: fourteen events each pull both pilots to one centre. What does part them
with physics and dragging live is weight. At a mass of 25 they rest 120px apart
(190px at 50, where the events bunch into two clumps). So b and c give the two
pilots a mass, and no position.
"""

import argparse
import json
import threading
from pathlib import Path

from graph7ph import app, render
from graph7ph.query import Subgraph
from graph7ph.theme import TOKENS

_ctx = threading.local()  # the view of the graph being drawn on this thread

SUBJECT_SIZE = 40  # a subject's dot, in vis.js units; every other dot is 25
SUBJECT_PX = 15  # a subject's name on screen; every other name is 12
PILOT_MASS = 25  # each pilot of a head-to-head; every other node is 1
DIM = 0.82  # how far a node outside the neighbourhood is taken toward the ground
ZOOMED = 1.5  # in c, the zoom past the fitted one at which every name is drawn


def _subjects(view: str, subgraph: Subgraph) -> list[str]:
    """The nodes a graph was asked about: the pilot or pilots, the card, the seeds."""
    match view:
        case "pilot_neighbourhood" | "pilot_affinity":
            return [n.id for n in subgraph.nodes if n.kind == "Pilot"]
        case "card_usage":
            return [n.id for n in subgraph.nodes if n.kind == "Card"]
        case "card_cooccurrence":
            return [n.id for n in subgraph.nodes if (n.group or "").startswith("seed:")]
    return []


# Runs before pyvis draws: what has to be in the data when the layout is solved.
_BEFORE = """
<script>
  window.P331 = (() => {
    let variant = null;
    try { variant = parent.document.documentElement.dataset.protoVariant; } catch (e) {}
    variant = variant || new URLSearchParams(location.search).get("variant") || "b";
    const SUBJECTS = __SUBJECTS__;
    const APART = __APART__;
    return {variant, SUBJECTS, before(nodes) {
      if (variant === "a") return;
      for (const id of SUBJECTS) {
        const mark = {id, size: __SUBJECT_SIZE__};
        if (APART) mark.mass = __PILOT_MASS__;
        nodes.update(mark);
      }
    }};
  })();
</script>
"""

# Runs after the shipped settle and answer scripts.
_AFTER = """
<script>
  (() => {
    const P = window.P331;
    if (P.variant === "a") return;
    const INK = "__INK__", GROUND = "__GROUND__", CLEAR = "rgba(0,0,0,0)";
    const body = network.body;
    const subjects = new Set(P.SUBJECTS.filter((id) => body.nodes[id]));
    const hubLabels = P.variant === "c" && !PINNED;

    function mix(hex, toward, t) {
      const at = (h, i) => parseInt(h.slice(1 + 2 * i, 3 + 2 * i), 16);
      return "#" + [0, 1, 2].map((i) => Math.round(at(hex, i) * (1 - t) + at(toward, i) * t)
        .toString(16).padStart(2, "0")).join("");
    }

    // What each node and edge is drawn with at rest, kept so it can be put back.
    const nodeRest = {}, edgeRest = {};
    const nodeOrder = [...body.nodeIndices], edgeOrder = [...body.edgeIndices];
    for (const id of nodeOrder) {
      const c = body.nodes[id].options.color;
      nodeRest[id] = {bg: c.background, border: c.border,
                      dimBg: mix(c.background, GROUND, __DIM__),
                      dimBorder: mix(c.border, GROUND, __DIM__)};
    }
    for (const id of edgeOrder) {
      const edge = body.edges[id], f = edge.labelModule.fontOptions;
      edgeRest[id] = {colour: edge.options.color.color, width: edge.options.width,
                      dim: mix(edge.options.color.color, GROUND, __DIM__),
                      font: {color: f.color, strokeColor: f.strokeColor, size: f.size,
                             strokeWidth: f.strokeWidth}};
    }
    const DIM_INK = mix(INK, GROUND, 0.72);

    // c: the names drawn at rest. The subject, what it reaches, and any hub.
    const named = new Set(subjects);
    for (const id of subjects) for (const other of network.getConnectedNodes(id)) named.add(other);
    for (const id of nodeOrder) if (network.getConnectedEdges(id).length >= 3) named.add(id);

    let hovered = null, fitted = null;

    function write(module, font) {
      Object.assign(module.fontOptions, font);
      module.labelDirty = true;
    }

    function paint() {
      const scale = network.getScale();
      const selected = network.getSelectedNodes()[0];
      const focus = hovered !== null ? hovered : (selected !== undefined ? selected : null);
      const near = focus === null ? null : new Set([focus, ...network.getConnectedNodes(focus)]);
      const reach = focus === null ? null : new Set(network.getConnectedEdges(focus));
      const everyName = !hubLabels || (fitted !== null && scale > fitted * __ZOOMED__);

      for (const id of nodeOrder) {
        const node = body.nodes[id], rest = nodeRest[id];
        const out = near !== null && !near.has(id);
        const dim = out && !subjects.has(id);
        node.options.color.background = dim ? rest.dimBg : rest.bg;
        node.options.color.border = dim ? rest.dimBorder : rest.border;
        node.options.color.hover = {background: node.options.color.background,
                                    border: node.options.color.border};
        const shown = near !== null ? (near.has(id) || everyName || named.has(id))
                                    : (everyName || named.has(id));
        write(node.labelModule, {
          color: !shown ? CLEAR : (out ? DIM_INK : INK),
          // A halo parts two names drawn over one another. A dimmed name is given
          // none: its halo would be cut out of the lit nodes it lies across.
          strokeColor: !shown || out || node.options.shape === "circle" ? CLEAR : GROUND,
        });
      }
      for (const id of edgeOrder) {
        const edge = body.edges[id], rest = edgeRest[id];
        const reached = reach !== null && reach.has(id);
        const colour = reached ? INK : (reach !== null ? rest.dim : rest.colour);
        edge.options.color.color = edge.options.color.highlight = edge.options.color.hover = colour;
        edge.options.width = reached ? rest.width + PATH_PX / scale : rest.width;
        if (!edge.options.label) continue;
        if (reached) {
          // A reached edge says its quantity at the size of a name, in the ink.
          write(edge.labelModule, {color: INK, strokeColor: GROUND,
                                   size: LABEL_PX / scale, strokeWidth: HALO_PX / scale});
        } else if (hubLabels || reach !== null) {
          write(edge.labelModule, {...rest.font, color: CLEAR, strokeColor: CLEAR});
        } else {
          write(edge.labelModule, rest.font);
        }
      }
      // Drawn in order, so the neighbourhood goes last and lands on top, and a
      // subject over whatever the layout has left lying across it.
      const last = (order, set) => [...order.filter((id) => !set.has(id)),
                                    ...order.filter((id) => set.has(id))];
      const front = last(near === null ? nodeOrder : last(nodeOrder, near), subjects);
      body.nodeIndices.splice(0, body.nodeIndices.length, ...front);
      body.edgeIndices.splice(0, body.edgeIndices.length,
                              ...(reach === null ? edgeOrder : last(edgeOrder, reach)));
      network.redraw();
    }

    function afterSettle() {
      fitted = network.getScale();
      // The edge widths are this script's now: a reached edge is drawn the same
      // whether its node is hovered or selected.
      network.setOptions({interaction: {hover: true},
                          edges: {selectionWidth: 0, hoverWidth: 0}});
      for (const id of subjects) {
        body.nodes[id].setOptions({font: {size: __SUBJECT_PX__ / fitted}});
      }
      paint();
    }

    const shippedSettle = window.settle;
    window.settle = function () {
      const was = settledFrame;
      shippedSettle();
      if (settledFrame !== was) afterSettle();
    };
    if (settledFrame !== null) afterSettle();

    network.on("hoverNode", (params) => { hovered = params.node; paint(); });
    network.on("blurNode", () => { hovered = null; paint(); });
    network.on("selectNode", paint);
    network.on("deselectNode", paint);
    network.on("zoom", () => { if (hubLabels) paint(); });
    // A row of the panel selects its node without an event of vis.js's own.
    const shippedSelect = network.selectNodes.bind(network);
    network.selectNodes = (...args) => { shippedSelect(...args); paint(); };
    window.P331.paint = paint;
  })();
</script>
"""

_HOOK = "network = new vis.Network(container, data, options);"


def _render(subgraph: Subgraph) -> str:
    doc = _shipped_render(subgraph)
    view = getattr(_ctx, "view", "")
    subjects = _subjects(view, subgraph)
    before = (_BEFORE.replace("__SUBJECTS__", _as_script_json(subjects))
              .replace("__APART__", "true" if view == "pilot_neighbourhood"
                       and len(subjects) == 2 else "false")
              .replace("__SUBJECT_SIZE__", str(SUBJECT_SIZE))
              .replace("__PILOT_MASS__", str(PILOT_MASS)))
    after = (_AFTER.replace("__INK__", TOKENS["text"]).replace("__GROUND__", TOKENS["surface"])
             .replace("__DIM__", str(DIM)).replace("__ZOOMED__", str(ZOOMED))
             .replace("__SUBJECT_PX__", str(SUBJECT_PX)))
    if _HOOK not in doc:
        raise RuntimeError("pyvis no longer builds the network where the prototype hooks in")
    doc = doc.replace(_HOOK, "window.P331.before(nodes); " + _HOOK, 1)
    doc = render._inject(doc, "</head>", before + "</head>")
    return render._inject(doc, "</body>", after + "</body>")


def _as_script_json(value) -> str:
    return (json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


# --- what is bolted onto the page ----------------------------------------------------

_PAGE_CSS = """
#proto-bar {
  position: fixed; left: 50%; bottom: 14px; transform: translateX(-50%); z-index: 99999;
  display: flex; align-items: center; gap: 0.25rem; padding: 0.3rem 0.35rem;
  border-radius: 999px; background: #fdf6e3; color: #111;
  font: 600 13px/1 system-ui, sans-serif; box-shadow: 0 8px 28px rgba(0, 0, 0, 0.6);
}
#proto-bar button {
  width: 28px; height: 28px; border: 0; border-radius: 999px; cursor: pointer;
  background: #111; color: #fdf6e3; font: inherit;
}
#proto-bar span { padding: 0 0.6rem; white-space: nowrap; }
"""

_PAGE_JS = """
() => {
  const VARIANTS = [["a", "Today"], ["b", "Subject + focus"], ["c", "Hub labels"]];
  const url = new URL(location.href);
  let at = VARIANTS.findIndex((v) => v[0] === url.searchParams.get("variant"));
  if (at < 0) at = 1;
  const bar = document.createElement("div");
  bar.id = "proto-bar";
  const prev = document.createElement("button"); prev.textContent = "\\u2190";
  const label = document.createElement("span");
  const next = document.createElement("button"); next.textContent = "\\u2192";
  bar.append(prev, label, next);
  document.body.append(bar);
  function apply(redraw) {
    const [key, name] = VARIANTS[at];
    document.documentElement.dataset.protoVariant = key;
    url.searchParams.set("variant", key);
    history.replaceState(null, "", url);
    label.textContent = "Prototype #331 \\u00b7 " + key.toUpperCase() + " (" + name + ")";
    // Each graph reads the variant as it is drawn, so a switch draws them again.
    if (redraw) document.querySelectorAll(".graph-split iframe").forEach((frame) => {
      frame.srcdoc = frame.srcdoc;
    });
  }
  function step(by) { at = (at + by + VARIANTS.length) % VARIANTS.length; apply(true); }
  prev.addEventListener("click", () => step(-1));
  next.addEventListener("click", () => step(1));
  document.addEventListener("keydown", (event) => {
    if (event.target.closest("input, textarea, select, [contenteditable], [role=tab]")) return;
    if (event.key === "ArrowLeft") step(-1);
    if (event.key === "ArrowRight") step(1);
  });
  apply(false);
}
"""


# --- start-up: swap the shipped modules' attributes, then launch the real app --------

_shipped_render = app.render_subgraph
_shipped_captions = app._graph_captions


def _captions(view, values, subgraph):
    # `_drawn_card` asks for the captions just before it renders, on the same thread,
    # which is where the renderer learns which view it is drawing.
    _ctx.view = view
    return _shipped_captions(view, values, subgraph)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=Path("../graph-7ph/data/graph"))
    args = parser.parse_args()

    app.render_subgraph = _render
    app._graph_captions = _captions

    kwargs = app.launch_kwargs()
    kwargs["css"] += _PAGE_CSS
    kwargs["js"] = _PAGE_JS
    app.build_app(args.db).launch(**kwargs)


if __name__ == "__main__":
    main()
