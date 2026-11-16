"""PROTOTYPE for issue #330. Throwaway: no tests, never merged into main.

The question: should a graph sit beside a panel that answers a node click, and what
should that panel hold? Three variants on the real app and the real data, switched by
``?variant=`` and by the pill at the foot of the page (arrow keys work too):

  a  Today          the page as it ships: key over the graph, details under it
  b  Side panel     the ticket: graph two thirds, panel one third (key, details, list)
  c  Floating card  the graph keeps the full width, a click is answered by a card over it

Run from the worktree, against the main checkout's built graph::

    GRADIO_SERVER_PORT=7890 uv run python -m graph7ph.prototype_330

Everything here is bolted onto the shipped modules from outside (module attributes
swapped at start-up), so no production file is edited. The node facts are fetched
with queries of the prototype's own; the build-out would carry them on the subgraph.
"""

import argparse
import json
import threading
from pathlib import Path

import ladybug

from graph7ph import app, numfmt, render, theme
from graph7ph.db import rows
from graph7ph.query import GEM_TOP_CUT, Subgraph, pilot_record

_ctx = threading.local()  # the view and values of the graph being drawn on this thread
_db = None  # the app's own database handle, caught as it is opened

SHORT_LIST = 10  # rows a side list shows before pointing at the full table below
LINKED = 8  # neighbours a clicked node lists


# --- facts the panel shows -----------------------------------------------------------


def _event_dates(conn, events: set[str]) -> dict:
    return dict(rows(conn.execute(
        """MATCH (f:Deck)-[:PLAYED_AT]->(e:Event) WHERE e.event IN $events
           RETURN e.event, min(f.createdAt)""", {"events": sorted(events)})))


def _deck_facts(conn, deck_ids: list[str]) -> dict[str, dict]:
    if not deck_ids:
        return {}
    found = list(rows(conn.execute(
        """MATCH (p:Pilot)<-[:PILOTED_BY]-(d:Deck)-[:PLAYED_AT]->(e:Event)
           WHERE d.deckId IN $ids
           OPTIONAL MATCH (d)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype)
           RETURN d.deckId, d.deckName, p.displayName, e.event, a.name, d.placement,
                  d.placementImputed, e.fieldSize, e.fieldImputed""", {"ids": deck_ids})))
    dates = _event_dates(conn, {row[3] for row in found})
    facts = {}
    for deck_id, name, pilot, event, archetype, place, place_rule, field, field_rule in found:
        finish = "Not on record" if place is None else numfmt.count_of(
            place, field, count_imputed=place_rule is not None,
            total_imputed=field_rule is not None)
        facts[deck_id] = {
            "name": name, "pilot": pilot, "event": event, "archetype": archetype or "",
            "finish": finish, "date": dates[event], "day": app._day(dates[event]),
        }
    return facts


def _event_facts(conn, events: list[str]) -> dict[str, dict]:
    if not events:
        return {}
    dates = _event_dates(conn, set(events))
    return {
        event: {"day": app._day(dates[event]),
                "field": f"{field:,}{numfmt.IMPUTED_MARK if rule is not None else ''} decks"}
        for event, field, rule in rows(conn.execute(
            "MATCH (e:Event) WHERE e.event IN $events "
            "RETURN e.event, e.fieldSize, e.fieldImputed", {"events": events}))
    }


def _rate(edge) -> str:
    return f"{numfmt.count_of(edge.decks, edge.total_decks)} · {app._edge_share(edge)}"


def _colours(subgraph: Subgraph) -> dict[str, str]:
    """Each node's drawn colour, worked out the way ``render_subgraph`` does."""
    groups = sorted({n.group for n in subgraph.nodes if n.group is not None})
    palette = {g: render._GROUP_COLOURS[i % 3] for i, g in enumerate(groups)}
    return {n.id: palette[n.group] if n.group is not None else render._KIND_COLOURS[n.kind]
            for n in subgraph.nodes}


def _meta(view: str, values: dict, subgraph: Subgraph, conn) -> dict:
    """What the panel says about each node: fact rows, a link, and its neighbours."""
    by_id = {n.id: n for n in subgraph.nodes}
    colour = _colours(subgraph)
    decks = _deck_facts(
        conn, [n.id.removeprefix("deck:") for n in subgraph.nodes if n.kind == "Deck"])
    events = _event_facts(conn, [n.label for n in subgraph.nodes if n.kind == "Event"])
    incoming, around = {}, {n.id: [] for n in subgraph.nodes}
    for edge in subgraph.edges:
        incoming.setdefault(edge.target, edge)
        around[edge.source].append((edge.target, edge))
        around[edge.target].append((edge.source, edge))

    def near(node_id: str, kind: str) -> list:
        return [by_id[other] for other, _ in around[node_id] if by_id[other].kind == kind]

    def facts(node) -> tuple[list, str | None]:
        edge_in = incoming.get(node.id)
        match node.kind:
            case "Deck":
                deck_id = node.id.removeprefix("deck:")
                f = decks[deck_id]
                return ([["Pilot", f["pilot"]], ["Event", f["event"]],
                         ["Finish", f["finish"]], ["Registered", f["day"]],
                         ["Archetype", f["archetype"] or "None given"]],
                        render.moxfield_url(deck_id))
            case "Placement":
                deck_id = node.id.removeprefix("placement:")
                f = decks[deck_id]
                return ([["Deck", f["name"]], ["Event", f["event"]],
                         ["Finish", f["finish"]], ["Registered", f["day"]]],
                        render.moxfield_url(deck_id))
            case "Event":
                f = events[node.label]
                return [["Registered", f["day"]], ["Field", f["field"]]], None
            case "Pilot":
                if view == "pilot_affinity":
                    return [["Decks on record", f"{len(pilot_record(conn, values['pilot'])):,}"],
                            ["Broad classes", str(len(near(node.id, "Macro")))]], None
                return [["Events drawn", str(len(near(node.id, "Event")))]], None
            case "Macro" | "Archetype" if view == "pilot_affinity":
                out = [["Events played", str(node.weight)]]
                if node.kind == "Archetype":
                    out.append(["Class", ", ".join(m.label for m in near(node.id, "Macro"))])
                else:
                    out.append(["Archetypes", str(len(near(node.id, "Archetype")))])
                return out, None
            case "Macro" | "Archetype" if view == "card_usage":
                out = [["Decks running it", numfmt.count_of(edge_in.decks, edge_in.total_decks)],
                       ["Share", app._edge_share(edge_in)]]
                if node.kind == "Archetype":
                    out.append(["Class", by_id[edge_in.source].label])
                else:
                    out.append(["Archetypes", str(len(near(node.id, "Archetype")))])
                return out, None
            case "Archetype":  # the gem view
                return [["Scored decks", f"{node.decks:,}"],
                        ["Gems", str(len(near(node.id, "Card")))]], None
            case "Intersection":
                return [["Decks running both", f"{node.decks:,}"]], None
            case "Card" if node.gem_luck is not None:
                return [["Archetype", by_id[edge_in.source].label],
                        ["Decks running it", numfmt.count_of(node.decks, node.total_decks)],
                        [f"In top {GEM_TOP_CUT:.0%}", f"{node.top_decks} of {node.decks}"],
                        ["Pilots", str(node.pilots)],
                        ["By chance", app._gem_luck_label(node.gem_luck)]], None
            case "Card" if node.decks is not None:
                return [["Decks running it",
                         numfmt.count_of(node.decks, node.total_decks, "decks")],
                        ["Share", numfmt.share(node.decks / node.total_decks)]], None
            case "Card" if edge_in is not None and edge_in.decks is not None:
                return [[f"Decks with {by_id[edge_in.source].label.split(' · ')[0]}",
                         numfmt.count_of(edge_in.decks, edge_in.total_decks)],
                        ["Rate", app._edge_share(edge_in)]], None
            case _:
                to_hub = next((edge for other, edge in around[node.id]
                               if by_id[other].kind == "Intersection"), None)
                if to_hub is not None:  # a seed of the two-card view
                    return [["Decks running both",
                             numfmt.count_of(to_hub.decks, to_hub.total_decks)],
                            ["Share of its decks", app._edge_share(to_hub)]], None
                return [["Cards drawn beside it", str(len(around[node.id]))]], None

    def note(other, edge) -> str:
        if other.kind == "Deck":
            return decks[other.id.removeprefix("deck:")]["finish"]
        if edge.decks is not None and edge.total_decks is not None:
            return app._edge_share(edge)
        if edge.events is not None:
            return f"{edge.events} event{'' if edge.events == 1 else 's'}"
        return ""

    meta = {}
    for node in subgraph.nodes:
        fact_rows, link = facts(node)
        linked = []
        if node.kind not in ("Deck", "Placement") and len(around[node.id]) > 1:
            linked = [[other, by_id[other].label, colour[other], note(by_id[other], edge)]
                      for other, edge in around[node.id]]
        meta[node.id] = {
            "kind": node.kind, "label": node.label, "colour": colour[node.id],
            "rows": fact_rows, "link": link,
            "linked": linked[:LINKED], "more": max(0, len(linked) - LINKED),
        }
    return meta


def _side_list(view: str, values: dict, subgraph: Subgraph, conn) -> dict | None:
    """The short list the side panel carries for this graph. A row is
    ``[node id, [cell, ...]]``, or ``[None, [group name]]`` for a heading row; a cell
    is a string or ``[text, line under it]``."""
    names = {n.id: n.label for n in subgraph.nodes}
    kinds = {n.id: n.kind for n in subgraph.nodes}

    def capped(found: list, noun: str, where: str) -> tuple[list, str]:
        if len(found) <= SHORT_LIST:
            return found, ""
        return found[:SHORT_LIST], f"Top {SHORT_LIST} of {len(found):,} {noun}. {where}"

    match view:
        case "pilot_neighbourhood" if not values.get("pilot2"):
            record = pilot_record(conn, values["pilot"])
            shown, said = capped(record, "decks", "The whole record is below the graphs.")
            return {
                "title": "Recent results", "caption": said.replace("Top", "Latest"),
                "cols": [["Event", 0], ["Archetype", 0], ["Finish", 1]],
                "rows": [[f"deck:{d.deck_id}", [
                    [d.event, app._day(d.date)], d.archetype or "",
                    "" if d.placement is None else numfmt.count_of(
                        d.placement, d.field_size,
                        count_imputed=d.placement_imputed is not None,
                        total_imputed=d.field_imputed is not None)]] for d in shown],
            }
        case "pilot_neighbourhood":
            decks = _deck_facts(conn, [i.removeprefix("deck:") for i, k in kinds.items()
                                       if k == "Deck"])
            pilots = [n.label for n in subgraph.nodes if n.kind == "Pilot"]
            shared: dict[str, dict] = {}
            for f in decks.values():
                shared.setdefault(f["event"], {"date": f["date"], "day": f["day"]})[
                    f["pilot"]] = f["finish"]
            ordered = sorted(shared.items(), key=lambda kv: kv[1]["date"], reverse=True)
            return {
                "title": "Shared events", "caption": f"{len(ordered)} events both played, "
                "newest first, with each pilot's finish.",
                "cols": [["Event", 0], *[[p, 1] for p in pilots]],
                "rows": [[f"event:{event}", [[event, f["day"]],
                                             *[f.get(p, "") for p in pilots]]]
                         for event, f in ordered],
            }
        case "pilot_affinity":
            macro = {e.target: names[e.source] for e in subgraph.edges
                     if kinds[e.source] == "Macro"}
            played = sorted((n for n in subgraph.nodes if n.kind == "Archetype"),
                            key=lambda n: (-n.weight, n.label))
            shown, said = capped(played, "archetypes", "")
            return {
                "title": "Archetypes played", "caption": said,
                "cols": [["Archetype", 0], ["Class", 0], ["Events", 1]],
                "rows": [[n.id, [n.label, macro.get(n.id, ""), str(n.weight)]] for n in shown],
            }
        case "card_usage":
            ranked = sorted(
                (e for e in subgraph.edges if kinds[e.target] == "Archetype"),
                key=lambda e: (-e.decks / e.total_decks, -e.total_decks, names[e.target]))
            shown, said = capped(ranked, "archetypes", "All of them are in the table below.")
            return {
                "title": "Archetypes by rate", "caption": said,
                "cols": [["Archetype", 0], ["Decks", 1], ["Share", 1]],
                "rows": [[e.target, [names[e.target],
                                     numfmt.count_of(e.decks, e.total_decks),
                                     app._edge_share(e)]] for e in shown],
            }
        case "card_cooccurrence":
            hub = next((n for n in subgraph.nodes if n.kind == "Intersection"), None)
            source = hub or app._seeds(subgraph)[0]
            partners = [e for e in subgraph.edges if e.source == source.id]
            shown, said = capped(partners, "cards", "All of them are in the table below.")
            return {
                "title": "Played with", "caption": said,
                "cols": [["Card", 0], ["Decks together", 1], ["Rate", 1]],
                "rows": [[e.target, [names[e.target],
                                     numfmt.count_of(e.decks, e.total_decks),
                                     app._edge_share(e)]] for e in shown],
            }
        case "meta_gems":
            inside = {e.target: e.source for e in subgraph.edges
                      if kinds[e.source] == "Archetype"}
            gems = sorted((n for n in subgraph.nodes if n.kind == "Card"),
                          key=lambda n: (names[inside[n.id]], n.gem_luck, n.label))
            out, last = [], None
            for gem in gems:
                if inside[gem.id] != last:
                    last = inside[gem.id]
                    out.append([None, [names[last]]])
                out.append([gem.id, [gem.label, str(gem.decks), str(gem.top_decks),
                                     str(gem.pilots), app._gem_luck_label(gem.gem_luck)]])
            return {
                "title": "The gems", "caption": "",
                "cols": [["Card", 0], ["Decks running it", 1],
                         [f"In top {GEM_TOP_CUT:.0%}", 1], ["Pilots", 1], ["By chance", 1]],
                "rows": out,
            }
    return None


# --- what is bolted onto the graph document ------------------------------------------

_T = theme.TOKENS
_DOC_STYLE = theme._font_faces() + f"""
  #proto-side, #proto-float {{ display: none; }}

  /* b: the graph two thirds, the panel one third */
  body[data-variant="b"] {{
    display: grid; grid-template-columns: minmax(0, 2fr) minmax(300px, 1fr);
    grid-template-rows: 100%;
  }}
  body[data-variant="b"] > #graph-legend, body[data-variant="b"] > #node-details,
  body[data-variant="c"] > #node-details {{ display: none; }}
  body[data-variant="b"] > .card {{ grid-column: 1; grid-row: 1; min-width: 0; height: 100%; }}
  body[data-variant="b"] > #proto-side {{
    display: block; grid-column: 2; grid-row: 1; min-width: 0; overflow-y: auto;
    border-left: 1px solid {_T['border']};
  }}
  @media (max-width: 760px) {{
    body[data-variant="b"] {{ grid-template-columns: 100%; grid-template-rows: auto 525px; }}
    body[data-variant="b"] > #proto-side {{
      grid-column: 1; grid-row: 1; border-left: 0; overflow: visible;
      border-bottom: 1px solid {_T['border']};
    }}
    body[data-variant="b"] > .card {{ grid-column: 1; grid-row: 2; }}
  }}

  /* c: the key and the answer float over a full-width graph */
  body[data-variant="c"] {{ position: relative; }}
  body[data-variant="c"] > #graph-legend {{
    position: absolute; top: 10px; left: 12px; z-index: 5; max-width: 55%;
    border: 0; border-radius: 6px; padding: 0.4rem 0.65rem; background: {_T['surface']}d9;
  }}
  body[data-variant="c"] > #proto-float:not([hidden]) {{
    display: block; position: absolute; top: 12px; right: 12px; z-index: 6;
    width: 320px; max-height: calc(100% - 24px); overflow-y: auto;
    padding: 0.9rem 1rem; border: 1px solid {_T['border']}; border-radius: 10px;
    background: {_T['surface-2']}; box-shadow: 0 10px 28px rgba(0, 0, 0, 0.4);
  }}

  .ps-section {{ padding: 0.85rem 1rem; border-bottom: 1px solid {_T['border']}; }}
  .ps-section:last-child {{ border-bottom: 0; }}
  .ps-section:empty {{ display: none; }}
  .ps-eyebrow {{
    margin: 0 0 0.5rem; color: {_T['text-mute']}; font-size: 11px; font-weight: 600;
    letter-spacing: 0.06em; text-transform: uppercase;
  }}
  #proto-key .chips {{ display: flex; flex-wrap: wrap; gap: 0.35rem 0.85rem; }}
  .pd-prompt, .ps-caption {{ margin: 0; color: {_T['text-mute']}; font-size: 13px; }}
  .ps-caption {{ margin: -0.25rem 0 0.5rem; }}
  .pd-kind {{
    display: flex; align-items: center; gap: 0.4rem; color: {_T['text-dim']};
    font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
  }}
  .pd-name {{
    margin: 0.15rem 0 0.6rem; font-family: {theme.DISPLAY_STACK}; font-weight: 600;
    font-size: 20px; line-height: 1.2; overflow-wrap: anywhere;
  }}
  .pd-rows {{
    display: grid; grid-template-columns: max-content 1fr; gap: 0.3rem 0.9rem;
    margin: 0; font-size: 14px;
  }}
  .pd-rows dt {{
    color: {_T['text-mute']}; font-size: 11px; letter-spacing: 0.06em;
    text-transform: uppercase; line-height: 1.9;
  }}
  .pd-rows dd {{ margin: 0; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }}
  .pd-link {{
    display: inline-block; margin-top: 0.7rem; color: {_T['accent-bright']};
    font-size: 14px; text-decoration: none;
  }}
  .pd-link:hover {{ text-decoration: underline; }}
  .pd-linked {{ margin-top: 0.9rem; }}
  .pd-linked ul {{ list-style: none; margin: 0; padding: 0; font-size: 13px; }}
  .pd-linked li {{
    display: flex; align-items: baseline; gap: 0.45rem; padding: 0.22rem 0.3rem;
    margin: 0 -0.3rem; border-radius: 4px; cursor: pointer;
  }}
  .pd-linked li:hover {{ background: {_T['border']}66; }}
  .pd-linked .name {{ flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }}
  .pd-linked .note, .pd-more {{ color: {_T['text-dim']}; font-variant-numeric: tabular-nums; }}
  .pd-more {{ margin: 0.3rem 0 0; font-size: 12px; color: {_T['text-mute']}; }}
  .pl-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .pl-table th {{
    padding: 0 0 0.35rem; text-align: left; vertical-align: bottom;
    color: {_T['text-mute']}; font-size: 11px; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; border-bottom: 1px solid {_T['border']};
  }}
  .pl-table td {{
    padding: 0.35rem 0; vertical-align: top; border-bottom: 1px solid {_T['border']}80;
  }}
  .pl-table th + th, .pl-table td + td {{ padding-left: 0.7rem; }}
  .pl-table .num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
  .pl-table .sub {{ display: block; color: {_T['text-mute']}; font-size: 11px; }}
  .pl-table tr.group td {{
    padding-top: 0.7rem; color: {_T['text-dim']}; font-weight: 600;
  }}
  .pl-table tr.pick {{ cursor: pointer; }}
  .pl-table tr.pick:hover td {{ background: {_T['border']}55; }}
  .pl-table tr.on td {{ background: {_T['accent']}26; }}
"""

_DOC_BODY = """
<aside id="proto-side">
  <section class="ps-section" id="proto-key"></section>
  <section class="ps-section" id="proto-details"></section>
  <section class="ps-section" id="proto-list"></section>
</aside>
<div id="proto-float" hidden></div>
<script>
  // PROTOTYPE #330. `network`, `settle`, `hasSettled`, `settledFrame` and
  // `readerHasView` are the shipped document's own.
  const P_META = __META__;
  const P_LIST = __LIST__;
  const P_PROMPT = "Click a node, or a row below, to see its details.";
  const P_FACE = "Hanken Grotesk";
  const P_HEIGHT = {
    a: "clamp(525px, 90vh, 950px)",
    b: "clamp(525px, 78vh, 760px)",
    c: "clamp(525px, 78vh, 760px)",
  };
  const side = document.getElementById("proto-side");
  const sideDetails = document.getElementById("proto-details");
  const floating = document.getElementById("proto-float");
  const listRows = {};

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function dot(colour) {
    const d = el("span", "legend-dot");
    d.style.background = colour;
    d.style.flex = "0 0 auto";
    return d;
  }

  function details(id) {
    const m = P_META[id];
    const box = document.createDocumentFragment();
    const kind = el("div", "pd-kind");
    kind.append(dot(m.colour), el("span", "", m.kind));
    box.append(kind, el("h2", "pd-name", m.label));
    const rows = el("dl", "pd-rows");
    for (const [k, v] of m.rows) rows.append(el("dt", "", k), el("dd", "", v));
    box.append(rows);
    if (m.link) {
      const a = el("a", "pd-link", "Open the decklist on Moxfield \\u2197");
      a.href = m.link; a.target = "_blank"; a.rel = "noopener";
      box.append(a);
    }
    if (m.linked.length) {
      const linked = el("div", "pd-linked");
      const ul = el("ul");
      for (const [other, name, colour, note] of m.linked) {
        const li = el("li");
        li.append(dot(colour), el("span", "name", name), el("span", "note", note));
        li.addEventListener("click", () => pick(other));
        ul.append(li);
      }
      linked.append(el("p", "ps-eyebrow", "Linked to"), ul);
      if (m.more) linked.append(el("p", "pd-more", "and " + m.more + " more"));
      box.append(linked);
    }
    return box;
  }

  function show(id) {
    if (id && !P_META[id]) return;
    if (id) {
      sideDetails.replaceChildren(el("p", "ps-eyebrow", "Selected"), details(id));
      floating.replaceChildren(details(id));
    } else {
      sideDetails.replaceChildren(
        el("p", "ps-eyebrow", "Selected"), el("p", "pd-prompt", P_PROMPT));
    }
    floating.hidden = !id;
    for (const [rowId, tr] of Object.entries(listRows)) tr.classList.toggle("on", rowId === id);
    fitFrame();
  }
  function pick(id) {
    if (!P_META[id]) return;
    network.selectNodes([id]);
    show(id);
  }

  function buildKey() {
    const legend = document.getElementById("graph-legend");
    if (!legend) return;
    const chips = el("div", "chips");
    chips.innerHTML = legend.innerHTML;  // our own markup, already escaped upstream
    document.getElementById("proto-key").append(el("p", "ps-eyebrow", "Colour key"), chips);
  }
  function buildList() {
    if (!P_LIST) return;
    const host = document.getElementById("proto-list");
    host.append(el("p", "ps-eyebrow", P_LIST.title));
    if (P_LIST.caption) host.append(el("p", "ps-caption", P_LIST.caption));
    const table = el("table", "pl-table");
    const head = el("tr");
    for (const [name, numeric] of P_LIST.cols) head.append(el("th", numeric ? "num" : "", name));
    table.append(head);
    for (const [id, cells] of P_LIST.rows) {
      const tr = el("tr");
      if (id === null) {
        tr.className = "group";
        const td = el("td", "", cells[0]);
        td.colSpan = P_LIST.cols.length;
        tr.append(td);
      } else {
        cells.forEach((cell, i) => {
          const td = el("td", P_LIST.cols[i][1] ? "num" : "");
          if (Array.isArray(cell)) td.append(cell[0], el("span", "sub", cell[1]));
          else td.textContent = cell;
          tr.append(td);
        });
        if (P_META[id]) {
          tr.classList.add("pick");
          tr.addEventListener("click", () => pick(id));
          listRows[id] = tr;
        }
      }
      table.append(tr);
    }
    host.append(table);
  }

  // The frame is the parent's, and a prototype may reach up and resize it.
  function fitFrame() {
    const v = document.body.dataset.variant;
    if (!window.frameElement) return;
    const stacked = v === "b" && window.innerWidth <= 760;
    frameElement.style.height = stacked ? (side.offsetHeight + 525) + "px" : P_HEIGHT[v];
  }
  function protoResettle() {
    setTimeout(() => {
      readerHasView = false; settledFrame = null;
      if (hasSettled) settle();
    }, 220);
  }
  function applyVariant(v) {
    if (!P_HEIGHT[v]) v = "b";
    document.body.dataset.variant = v;
    const face = v === "a" ? "arial" : P_FACE;
    network.setOptions({nodes: {font: {face: face}}, edges: {font: {face: face}}});
    fitFrame();
    protoResettle();
  }

  buildKey();
  buildList();
  show(null);
  network.on("selectNode", (params) => show(params.nodes[0]));
  network.on("deselectNode", () => show(null));
  window.addEventListener("message", (event) => {
    if (event.data && event.data.protoVariant) applyVariant(event.data.protoVariant);
  });
  let startAs = "b";
  try { startAs = parent.document.documentElement.dataset.protoVariant || "b"; } catch (e) {}
  applyVariant(startAs);
  // Labels are measured when the graph settles, so settle again once the face is in.
  document.fonts.load("12px '" + P_FACE + "'").then(protoResettle);
</script>
"""


def _as_script_json(value) -> str:
    return (json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def _render_with_panel(subgraph: Subgraph) -> str:
    doc = _shipped_render(subgraph)
    view, values = _ctx.view, _ctx.values
    conn = ladybug.Connection(_db)
    body = (_DOC_BODY
            .replace("__META__", _as_script_json(_meta(view, values, subgraph, conn)))
            .replace("__LIST__", _as_script_json(_side_list(view, values, subgraph, conn))))
    doc = render._inject(doc, "</head>", f"<style>{_DOC_STYLE}</style></head>")
    return render._inject(doc, "</body>", body + "</body>")


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
/* b: the gems table lives in the side panel, so the one over the graph steps aside */
html[data-proto-variant="b"] .proto-gems-evidence { display: none; }
@media (max-width: 760px) {
  html[data-proto-variant="b"] .proto-gems-evidence { display: block; }
}
"""

_PAGE_JS = """
() => {
  const VARIANTS = [["a", "Today"], ["b", "Side panel"], ["c", "Floating card"]];
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
  function apply() {
    const [key, name] = VARIANTS[at];
    document.documentElement.dataset.protoVariant = key;
    url.searchParams.set("variant", key);
    history.replaceState(null, "", url);
    label.textContent = "Prototype #330 \\u00b7 " + key.toUpperCase() + " (" + name + ")";
    document.querySelectorAll("iframe").forEach((frame) => {
      if (frame.contentWindow) frame.contentWindow.postMessage({protoVariant: key}, "*");
    });
  }
  function step(by) { at = (at + by + VARIANTS.length) % VARIANTS.length; apply(); }
  prev.addEventListener("click", () => step(-1));
  next.addEventListener("click", () => step(1));
  document.addEventListener("keydown", (event) => {
    if (event.target.closest("input, textarea, select, [contenteditable], [role=tab]")) return;
    if (event.key === "ArrowLeft") step(-1);
    if (event.key === "ArrowRight") step(1);
  });
  apply();
}
"""


# --- start-up: swap the shipped modules' attributes, then launch the real app --------

_shipped_render = app.render_subgraph
_shipped_captions = app._graph_captions
_shipped_gem_table = app._gem_table
_shipped_open = app.open_database


def _captions(view, values, subgraph):
    # `graph_card` asks for the captions just before it renders, on the same thread,
    # which is where the renderer learns which view it is drawing.
    _ctx.view, _ctx.values = view, values
    return _shipped_captions(view, values, subgraph)


def _open(*args, **kwargs):
    global _db
    _db = _shipped_open(*args, **kwargs)
    return _db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=Path("../graph-7ph/data/graph"))
    args = parser.parse_args()

    app.render_subgraph = _render_with_panel
    app._graph_captions = _captions
    app._gem_table = lambda sg: f"<div class='proto-gems-evidence'>{_shipped_gem_table(sg)}</div>"
    app.open_database = _open

    kwargs = app.launch_kwargs()
    kwargs["css"] += _PAGE_CSS
    kwargs["js"] = _PAGE_JS
    app.build_app(args.db).launch(**kwargs)


if __name__ == "__main__":
    main()
