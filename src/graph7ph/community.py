"""The Community page: what is on record, drawn from a bundle's figures (issue #346).

The landing page. One line on what the format and the app are, then the community's
own numbers, which no other tab shows: six figures, every event as a dot, the pilots
on record over the events of each month, each year's pilots by the year they first
played, and a doorway to each content tab.

Pure markup and one page script. :func:`page` takes the ``community`` block of the
figures file (``figures._community``) and returns the HTML the app sets in a
``gr.HTML``; nothing on the page is typed in here but its fixed words. What each
class looks like is the stylesheet's (:func:`graph7ph.theme.build_css`).

The wording rule (``CONTEXT.md``): counts are of what is "on record", no sentence
says the community grew, and the source is named once. No pilot is named, and no
event but a major.
"""

import html
import json
from datetime import date

from graph7ph import numfmt, palette, parts, theme
from graph7ph.render import _KIND_COLOURS

TITLE = "Australian 7 Point Highlander"
LEDE = (
    "7 Point Highlander is a singleton Magic format run by the people who play it. "
    "This app maps the tournament results they report: events, pilots, decks and "
    "cards, with some extra depth for the Spikes among us."
)
SOURCE = "Built from results the community reports on Moxfield."

# A doorway to each content tab: the tab's own label, which is what a click opens
# (``app._JUMPS_JS``), and one line on what the tab answers. No numbers, so nothing
# above them on the page is repeated.
DOORS: tuple[tuple[str, str], ...] = (
    ("Metagame", "What the field plays, and how that has shifted year by year."),
    ("Archetypes", "One archetype's share, finishes and cards."),
    ("Cards", "Where a card is played, and what it is played beside."),
    ("Hidden gems", "Cards that finish better than their archetype usually does."),
    ("Pilots", "One pilot's events, decks and finishes."),
    ("Player leaderboard", "Standings across the major events."),
)


# Each figure's label carries the dot of its kind in the graph's own colours.
_KIND = {"pilots": "Pilot", "events": "Event", "decks": "Deck", "cards": "Card",
         "archetypes": "Archetype"}

def _dot(kind: str) -> str:
    return f"<span class='cm-dot' style='--k:{_KIND_COLOURS[kind]}'></span>"


def _figures(record: dict) -> str:
    """The six figures, each with the newest year's beneath."""
    latest = record["years"][-1]

    def figure(kind: str, label: str, value: int, beneath: str) -> str:
        return (
            f"<div class='cm-fig'><div class='cm-label t-control-label'>{_dot(kind)}{label}</div>"
            f"<div class='cm-value'>{value:,}</div>"
            f"<div class='cm-beneath'>{html.escape(beneath)}</div></div>"
        )

    largest = record["largest_field"]
    # Only a major is named, so a largest field that is none shows its year alone.
    where = ", ".join(str(part) for part in (largest["major"], largest["year"]) if part)
    return (
        "<section class='cm-figures' aria-label='The record in six numbers'>"
        + "".join(
            figure(kind, name.capitalize(), record["totals"][name],
                   f"{record['this_year'][name]:,} in {latest}")
            for name, kind in _KIND.items()
        )
        + figure("Event", "Largest field", largest["players"], where)
        + "</section>"
    )


def _card(title: str, caption: str | None, body: str) -> str:
    captions = (parts.caption(caption),) if caption else ()
    return (f"<section class='insight-card cm-card'>{parts.card_head(title, *captions)}"
            f"{body}</section>")


def _events_card(record: dict) -> str:
    """Every event as a dot. The dots are drawn by the page script, into the plot."""
    first, latest = record["years"][0], record["years"][-1]
    events = record["majors"] + record["other_events"]
    label = (f"{events:,} events from {first} to {latest}, one dot each, "
             "sized by players")
    event = _KIND_COLOURS["Event"]
    return _card(
        "Every event on record",
        "One dot per event, placed when its decks were registered and sized by its "
        "players.",
        f"<div class='cm-plot' data-chart='events' style='--k:{event}' role='img' "
        f"aria-label='{label}'></div>"
        "<div class='cm-key tabular'>"
        f"<span><i style='--k:{event}'></i>Major, a field over "
        f"{record['major_field_over']} that published its standings "
        f"({record['majors']:,})</span>"
        f"<span><i class='cm-ring' style='--k:{event}'></i>Other events "
        f"({record['other_events']:,})</span></div>",
    )


def _pair_card(record: dict) -> str:
    """Pilots on record over events each month: two panels on one time axis."""
    first, latest = record["years"][0], record["years"][-1]
    through = _day_and_month(record["through"])
    pilot, event = _KIND_COLOURS["Pilot"], _KIND_COLOURS["Event"]
    return _card(
        "Pilots on record, and events each month",
        "Everyone who has registered a deck, counted from their first. Events are "
        "counted in the month their decks were registered.",
        "<div class='cm-pair'>"
        "<div class='cm-sub t-control-label'>Pilots on record</div>"
        f"<div class='cm-plot' data-chart='pilots' style='--k:{pilot}' role='img' "
        "aria-label='Pilots on "
        f"record, {record['totals']['pilots']:,} by {through} {latest}'></div>"
        "<div class='cm-sub t-control-label'>Events each month</div>"
        f"<div class='cm-plot' data-chart='months' style='--k:{event}' role='img' "
        "aria-label='Events each "
        f"month, from {first} to {through} {latest}'></div>"
        "</div>",
    )


def _day_and_month(day: str) -> str:
    """An ISO day as ``4 Nov``."""
    when = date.fromisoformat(day)
    return f"{when.day} {when:%b}"


def _years_card(record: dict) -> str:
    """One bar per year, split by first-played year, with the year's total after it.

    A pilot who stopped appearing is in no bar after their last year and is
    counted nowhere as having left: the page shows it only as a band narrowing.
    """
    years = record["years"]
    latest = years[-1]
    hues = dict(zip(years, palette.first_played(len(years))))
    most = max(row["pilots"] for row in record["pilots_by_year"])

    def segment(row: dict, first: int, pilots: int) -> str:
        # The number is set in whichever of the page's two inks reads better on its hue.
        ink = max(("text", "bg"),
                  key=lambda ink: theme.contrast_ratio(theme.TOKENS[ink], hues[first]))
        share = f"{numfmt.share(pilots / row['pilots'])} of {row['year']}"
        return (
            f"<div class='cm-seg' tabindex='0' role='img' "
            f"style='--k:{hues[first]};color:var(--{ink});flex:{pilots} 0 0' "
            f"aria-label='{pilots:,} pilots, first played in {first}, {share}' "
            f"data-tip='{pilots:,} pilots' "
            f"data-tip-more='first played in {first} · {share}'>"
            f"<span>{pilots:,}</span></div>"
        )

    def bar(row: dict) -> str:
        part = (f"<small>to {_day_and_month(record['through'])}</small>"
                if row["year"] == latest else "")
        segments = "".join(
            segment(row, int(first), pilots)
            for first, pilots in row["first_played"].items()
        )
        return (
            f"<div class='cm-row'><div class='cm-year'>{row['year']}{part}</div>"
            "<div class='cm-track'>"
            f"<div class='cm-bar' style='--share:{row['pilots'] / most:.4f}'>{segments}</div>"
            f"<span class='cm-total'>{row['pilots']:,}</span></div></div>"
        )

    key = "".join(
        f"<span><i class='cm-square' style='--k:{hues[year]}'></i>First played {year}</span>"
        for year in years
    )
    this_year = record["this_year"]
    return _card(
        "Pilots each year, by the year they first played", None,
        f"<div class='cm-key'>{key}</div>"
        "<div class='cm-years tabular'>"
        + "".join(bar(row) for row in record["pilots_by_year"])
        + "</div>"
        f"<p class='cm-note tabular'><b>{this_year['played_before']:,}</b> of {latest}'s "
        f"{this_year['pilots']:,} pilots had played before. "
        f"<b>{record['played_every_year']:,}</b> have played every year since "
        f"{years[0]}.</p>",
    )


def _doors() -> str:
    return "<nav class='cm-doors' aria-label='What the app holds'>" + "".join(
        f"<a class='cm-door' href='#' data-tab='{html.escape(tab, quote=True)}'>"
        "<span class='cm-arrow' aria-hidden='true'>→</span>"
        f"<h3>{html.escape(tab)}</h3><p>{html.escape(line)}</p></a>"
        for tab, line in DOORS
    ) + "</nav>"


def _chart_data(record: dict) -> str:
    """What the page script draws from, as the value of a single-quoted attribute.

    Numbers, so the page carries each once and small: a date is its day since
    1970, a month its place counted from the first January on record, and an event
    is its day, its players, and its name where it is a major.
    """
    first = record["years"][0]

    def day(iso: str) -> int:
        return (date.fromisoformat(iso) - date(1970, 1, 1)).days

    def month(iso: str) -> int:
        year, month = iso.split("-")
        return (int(year) - first) * 12 + int(month) - 1

    data = {
        "years": record["years"],
        "through": day(record["through"]),
        "events": [[day(event["date"]), event["players"], event["major"]]
                   for event in record["events"]],
        "pilots": [[day(when), pilots] for when, pilots in record["pilots_on_record"]],
        "months": [[month(when), events] for when, events in record["events_by_month"]],
    }
    return html.escape(json.dumps(data, separators=(",", ":")), quote=False).replace(
        "'", "&#x27;")


def page(record: dict) -> str:
    """The Community page for the ``community`` block of a bundle's figures."""
    return (
        f"<div class='community' data-record='{_chart_data(record)}'>"
        "<section class='cm-intro'>"
        # The format's name keeps its last three words on one line.
        f"<h2 class='page-name'>{TITLE.replace(' ', '&nbsp;').replace('&nbsp;', ' ', 1)}</h2>"
        f"<p class='t-lede'>{LEDE}</p></section>"
        f"{_figures(record)}"
        f"<p class='cm-source'>{SOURCE}</p>"
        + _events_card(record) + _pair_card(record) + _years_card(record)
        + _doors()
        + "</div>"
    )


# The page script, run once when the page's block is in the document
# (``gr.HTML(js_on_load=...)``, which hands it the block as ``element``).
#
# It draws the three charts on a time axis (the dots, the running count and the
# months) from the numbers the page was handed, as SVG, and not with the chart
# library: the page is the first thing a visitor loads and the library is five
# times its weight. Each is drawn to the width it is given and drawn again when
# that changes, a phone turned on its side included, since a dot's size and where
# it settles depend on the room. The yearly bars are the page's own markup.
#
# One scale places a day across every chart, so the year lines of the three fall
# at the same positions down the page.
#
# A mark says its value in one box (``.cm-tip``): under the pointer, under a
# finger, where a tap leaves it up until the next tap or a scroll, and on
# keyboard focus for the marks that can take it, the parts of a year's bar.
SCRIPT = r"""
const root = element.querySelector('.community');
const D = JSON.parse(root.dataset.record);
const NS = 'http://www.w3.org/2000/svg';
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const fmt = (n) => n.toLocaleString('en-AU');
const first = D.years[0], last = D.years[D.years.length - 1];
const dayOf = (year, month = 0) => Date.UTC(year, month, 1) / 864e5;
const T0 = dayOf(first), T1 = dayOf(last + 1);
const scale = (width) => (day) => 1 + (day - T0) / (T1 - T0) * (width - 2);
const plot = (name) => root.querySelector(`[data-chart="${name}"]`);
const el = (tag, attrs, parent) => {
  const node = document.createElementNS(NS, tag);
  for (const name in attrs) node.setAttribute(name, attrs[name]);
  parent.appendChild(node);
  return node;
};
// The largest of 1, 2 and 5 times a power of ten that still rules an axis twice.
const step = (max) => {
  let best = 1;
  for (const unit of [1, 2, 5]) for (let s = unit; s <= max / 2; s *= 10) best = Math.max(best, s);
  return best;
};

// The one box a mark speaks in. `settle` undoes whatever the last mark lit.
const tip = document.createElement('div');
tip.className = 'cm-tip';
tip.setAttribute('role', 'status');
root.appendChild(tip);
let settle = () => {};
const hush = () => { settle(); settle = () => {}; tip.style.display = 'none'; };
const say = (x, y, strong, more, undo) => {
  settle();
  settle = undo || (() => {});
  tip.textContent = '';
  tip.appendChild(document.createElement('b')).textContent = strong;
  if (more) tip.appendChild(document.createTextNode(more));
  tip.style.display = 'block';
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.max(8, Math.min(x - w / 2, innerWidth - w - 8)) + 'px';
  tip.style.top = (y - h - 14 < 8 ? y + 18 : y - h - 14) + 'px';
};
// A finger lifted leaves the box up; a pointer moved off takes it down.
const leaving = (ev) => { if (ev.pointerType !== 'touch') hush(); };
document.addEventListener('pointerdown', (ev) => {
  if (!ev.target.closest || !ev.target.closest('.cm-plot, .cm-pair, .cm-seg')) hush();
});
// A scroll takes the box down, since it is set where the mark was. Not the box of
// the part the keyboard is on: focus is what scrolled the page to it, so that one
// is set again where the part now is.
let focused = null;
addEventListener('scroll', () => (focused ? focused() : hush()), {passive: true});

const yearLines = (svg, x, top, base, labelled) => {
  for (const year of [...D.years, last + 1]) {
    const at = x(dayOf(year));
    el('line', {class: 'cm-grid cm-yearline', 'data-year': year, x1: at, x2: at, y1: top, y2: base}, svg);
    if (labelled && year <= last) el('text', {x: at + 6, y: base + 17}, svg).textContent = year;
  }
};

// Every event on record: one dot each, along time, sized by its players, and
// moved up or down from the line until it touches no dot already set. The
// largest are set first, so they keep the line.
const drawEvents = () => {
  const host = plot('events');
  host.textContent = '';
  const W = host.clientWidth;
  if (!W) return;
  const k = Math.max(0.52, Math.min(1, W / 1100)), x = scale(W), GAP = 1.5;
  const dots = D.events.map(([day, players, major]) => ({
    day, players, major, x: x(day), y: 0, r: Math.max(2.2, Math.sqrt(players) * 0.95 * k),
  }));
  const placed = [];
  for (const dot of [...dots].sort((a, b) => b.r - a.r)) {
    const near = placed.filter((other) => Math.abs(other.x - dot.x) < other.r + dot.r + GAP);
    for (let i = 0; ; i++) {
      const y = Math.ceil(i / 2) * (i % 2 ? 1 : -1);
      if (near.every((other) => (other.x - dot.x) ** 2 + (other.y - y) ** 2 >= (other.r + dot.r + GAP) ** 2)) {
        dot.y = y;
        break;
      }
    }
    placed.push(dot);
  }
  const low = Math.min(...dots.map((dot) => dot.y - dot.r));
  const high = Math.max(...dots.map((dot) => dot.y + dot.r));
  const base = 10 + (high - low) + 10, line = 10 - low;
  const svg = el('svg', {width: W, height: base + 26, 'aria-hidden': 'true'}, host);
  yearLines(svg, x, 0, base, true);
  el('line', {class: 'cm-grid', x1: 0, x2: W, y1: base, y2: base}, svg);
  // A major is solid; any other event is a ring, drawn inside the room it was set in.
  for (const dot of dots) {
    dot.mark = el('circle', {
      class: dot.major ? 'cm-event cm-major' : 'cm-event', cx: dot.x, cy: line + dot.y,
      r: dot.major ? dot.r : dot.r - 0.6,
    }, svg);
  }
  // A dot wide enough to hold its own number shows it.
  for (const dot of dots) {
    if (dot.r >= 12.5) {
      el('text', {class: 'cm-on', x: dot.x, y: line + dot.y + 4, 'text-anchor': 'middle'}, svg)
        .textContent = dot.players;
    }
  }
  const point = (ev) => {
    const box = svg.getBoundingClientRect();
    const mx = ev.clientX - box.left, my = ev.clientY - box.top;
    let hit = null, gap = 14;
    for (const dot of dots) {
      const away = Math.hypot(dot.x - mx, line + dot.y - my) - dot.r;
      if (away < gap) { gap = away; hit = dot; }
    }
    if (!hit) return hush();
    // An event says its players and its year, and its name only if it is a major.
    const year = new Date(hit.day * 864e5).getUTCFullYear();
    say(box.left + hit.x, box.top + line + hit.y - hit.r, `${fmt(hit.players)} players`,
        hit.major ? `${hit.major} · ${year}` : `${year}`,
        () => hit.mark.classList.remove('cm-hot'));
    hit.mark.classList.add('cm-hot');
  };
  svg.addEventListener('pointermove', point);
  svg.addEventListener('pointerdown', point);
  svg.addEventListener('pointerleave', leaving);
};

// Pilots on record over events each month: two panels on the one time axis,
// with one line under the pointer through both.
const drawPair = () => {
  const pair = root.querySelector('.cm-pair'), above = plot('pilots'), below = plot('months');
  above.textContent = '';
  below.textContent = '';
  const W = above.clientWidth;
  if (!W) return;
  const x = scale(W), total = D.pilots[D.pilots.length - 1][1];

  // Above: everyone who has registered a deck, counted from their first.
  const H = W < 500 ? 190 : 250, top = 14, base = H - 6;
  const rule = step(total), most = Math.ceil(total / rule) * rule;
  const y = (pilots) => base - pilots / most * (base - top);
  const svg = el('svg', {width: W, height: H, 'aria-hidden': 'true'}, above);
  yearLines(svg, x, top, base, false);
  for (let v = 0; v <= most; v += rule) {
    el('line', {class: 'cm-grid', x1: 1, x2: W - 1, y1: y(v), y2: y(v)}, svg);
    if (v) el('text', {x: 7, y: y(v) - 6}, svg).textContent = fmt(v);
  }
  let d = `M${x(D.pilots[0][0])},${y(0)}`;
  for (const [day, pilots] of D.pilots) d += `H${x(day)}V${y(pilots)}`;
  d += `H${x(D.through)}`;
  el('path', {d: `${d}V${y(0)}Z`, fill: 'var(--k)', 'fill-opacity': 0.1}, svg);
  el('path', {class: 'cm-curve', d, fill: 'none', stroke: 'var(--k)', 'stroke-width': 2,
              'stroke-linejoin': 'round', 'stroke-linecap': 'round'}, svg);
  const end = x(D.through);
  el('circle', {cx: end, cy: y(total), r: 5, fill: 'var(--k)', stroke: 'var(--surface)', 'stroke-width': 2}, svg);
  el('text', W - end > 52
    ? {class: 'cm-ink', x: end + 10, y: y(total) + 4}
    : {class: 'cm-ink', x: end - 10, y: y(total) - 6, 'text-anchor': 'end'}, svg).textContent = fmt(total);

  // Below: the events of each month, a month with none drawn as none.
  const counts = new Map(D.months);
  const busiest = Math.max(...counts.values());
  const eTop = 18, eBase = 96, ceiling = busiest * 1.12;
  const ey = (events) => eBase - events / ceiling * (eBase - eTop);
  const svg2 = el('svg', {width: W, height: eBase + 26, 'aria-hidden': 'true'}, below);
  for (let v = step(ceiling); v <= ceiling; v += step(ceiling)) {
    el('line', {class: 'cm-grid', x1: 1, x2: W - 1, y1: ey(v), y2: ey(v)}, svg2);
    el('text', {x: 7, y: ey(v) - 5}, svg2).textContent = v;
  }
  yearLines(svg2, x, eTop - 8, eBase, true);
  el('line', {class: 'cm-grid', x1: 1, x2: W - 1, y1: eBase, y2: eBase}, svg2);
  const bars = new Map();
  let named = false;
  for (const [month, events] of D.months) {
    const from = x(dayOf(first, month)), slot = x(dayOf(first, month + 1)) - from;
    const wide = Math.max(2, Math.min(24, slot - 2)), left = from + (slot - wide) / 2;
    const up = ey(events), r = Math.min(4, wide / 2, eBase - up);
    bars.set(month, el('path', {
      class: 'cm-month', 'data-month': month, fill: 'var(--k)',
      d: `M${left},${eBase}V${up + r}Q${left},${up} ${left + r},${up}H${left + wide - r}`
        + `Q${left + wide},${up} ${left + wide},${up + r}V${eBase}Z`,
    }, svg2));
    // The busiest month carries its count.
    if (events === busiest && !named) {
      named = true;
      el('text', {class: 'cm-ink', x: left + wide / 2, y: up - 6, 'text-anchor': 'middle'}, svg2)
        .textContent = events;
    }
  }

  const hairs = [el('line', {class: 'cm-hair', y1: top, y2: base, visibility: 'hidden'}, svg),
                 el('line', {class: 'cm-hair', y1: eTop - 8, y2: eBase, visibility: 'hidden'}, svg2)];
  const mark = el('circle', {r: 4, fill: 'var(--k)', stroke: 'var(--surface)', 'stroke-width': 2,
                             visibility: 'hidden'}, svg);
  const point = (ev) => {
    const box = svg.getBoundingClientRect();
    const day = Math.max(D.pilots[0][0], Math.min(
      D.through, T0 + (ev.clientX - box.left - 1) / (W - 2) * (T1 - T0)));
    let pilots = 0;
    for (const [from, count] of D.pilots) { if (from > day) break; pilots = count; }
    const when = new Date(day * 864e5);
    const month = (when.getUTCFullYear() - first) * 12 + when.getUTCMonth();
    const events = counts.get(month) || 0, bar = bars.get(month), at = x(day);
    // The count is the line's at this point; the month is the bar's below it.
    say(box.left + at, box.top + y(pilots), `${fmt(pilots)} pilots on record`,
        `${events} ${events === 1 ? 'event' : 'events'} in `
        + `${MONTHS[when.getUTCMonth()]} ${when.getUTCFullYear()}`,
        () => {
          for (const hair of hairs) hair.setAttribute('visibility', 'hidden');
          mark.setAttribute('visibility', 'hidden');
          if (bar) bar.classList.remove('cm-hot');
        });
    for (const hair of hairs) {
      hair.setAttribute('x1', at);
      hair.setAttribute('x2', at);
      hair.setAttribute('visibility', 'visible');
    }
    mark.setAttribute('cx', at);
    mark.setAttribute('cy', y(pilots));
    mark.setAttribute('visibility', 'visible');
    if (bar) bar.classList.add('cm-hot');
  };
  pair.onpointermove = point;
  pair.onpointerdown = point;
  pair.onpointerleave = leaving;
};

// A part of a year's bar says its value on hover, on a tap and on focus, and
// keeps its number off the bar where the part is too short to hold it.
const segments = [...root.querySelectorAll('.cm-seg')];
for (const segment of segments) {
  const speak = () => {
    const box = segment.getBoundingClientRect();
    say(box.left + box.width / 2, box.top, segment.dataset.tip, segment.dataset.tipMore);
  };
  segment.addEventListener('pointerenter', speak);
  segment.addEventListener('focus', () => { focused = speak; speak(); });
  segment.addEventListener('pointerleave', leaving);
  segment.addEventListener('blur', () => { focused = null; hush(); });
}
const fit = () => {
  for (const segment of segments) {
    segment.classList.toggle(
      'cm-tight', segment.clientWidth < segment.firstElementChild.offsetWidth + 14);
  }
};

const draw = () => { hush(); drawEvents(); drawPair(); fit(); };
let drawn = 0;
new ResizeObserver(() => {
  const width = root.clientWidth;
  if (width && width !== drawn) { drawn = width; draw(); }
}).observe(root);
document.fonts.ready.then(() => { if (root.clientWidth) draw(); });
"""
