"""The v1 acceptance pass (issue #118): the screenshot matrix and the WCAG AA
contrast check, captured from the running app rather than asserted about the tokens.

Every tab and state is driven to on a real browser at phone and desktop width, and
each one is both photographed and measured: the screenshot is the visual evidence
#85 asks for, and the same visit walks every rendered text node (page, Plotly SVG,
and the pyvis iframe alike) and computes its contrast against the background it is
actually painted on. Measuring the live document is what makes the pass an
acceptance check rather than a restatement of ``theme.TOKENS``: it covers the
Gradio chrome the app does not author, the composited translucent surfaces, and
the SVG chart text no stylesheet rule can be read off.

Phone width is a real narrow viewport (Chromium device emulation), not a resized
window: the page reflows exactly as it does on a phone, which the screenshot tool
in the maintainer's browser cannot do (see #118).

Two states cannot be reached from the controls on the shipped corpus: nothing in
the graph exceeds the 250-node draw limit, and the gem dropdown only offers
archetypes whose slice can answer (ADR 0012). ``--forced`` lowers those two floors
so the refusals draw, and the shots it writes are named ``forced-`` to say so.

Usage (from the repo root, with the artifact at ``data/graph``)::

    uv run --with playwright python scripts/acceptance_shots.py
    uv run --with playwright python scripts/acceptance_shots.py --forced

Requires a one-off ``uv run --with playwright python -m playwright install chromium``.
"""

import argparse
import json
import socket
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

# The two widths #85 asks for: a phone, and a desktop. The phone is emulated as a
# device (touch, mobile UA, 2x pixels), so the page reflows at 390 CSS pixels the
# way it does in a hand; the desktop is captured at 1x, since a 2x full-page shot of
# a 1440-wide page is four times the bytes for evidence read at a glance.
VIEWPORTS = {
    "phone": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                  is_mobile=True, has_touch=True),
    "desktop": dict(viewport={"width": 1440, "height": 900}, device_scale_factor=1),
}

# Entities picked off the shipped artifact for states that need one, so each shot
# shows a real answer rather than a contrived one. Chosen for shape: a pilot with
# four scored years, a pair who met four times and a pair who never met, a card
# whose usage draws inside the limit.
PILOT = "Ariel M"
PILOT_THIN = "Aaron C"          # played, but no year with enough events to average
PILOT_B = "Alex V"              # four events shared with Adam D
PILOT_RIVAL = "Adam D"
PILOT_STRANGER = "Alex B"       # never met Adam D
CARD = "Snapcaster Mage"
CARD_B = "Lightning Bolt"
ARCHETYPE = "Grixis"
ARCHETYPE_B = "Walks"
ARCHETYPE_LONE = "Bogles"       # never ranked at an event with Jeskai Ascendancy
ARCHETYPE_LONE_B = "Jeskai Ascendancy"


def wait(page: Page, ms: int = 700) -> None:
    page.wait_for_timeout(ms)


def tab(page: Page, name: str) -> None:
    """Open a tab off the tab bar, at either width.

    One path since #172: the bar scrolls horizontally, so all seven tabs render on
    the strip at 390px and Gradio's "..." overflow menu (which held four of them
    there before the fix) hides itself, leaving nothing to fall through to."""
    page.locator("div.tab-container[role=tablist]").get_by_role(
        "tab", name=name, exact=True).first.click()
    wait(page, 900)


_SESSIONS: dict[int, object] = {}


def throttle(page: Page, latency_ms: int) -> None:
    """Hold the page's requests back by ``latency_ms``, or release them at 0.

    One CDP session per page, reused: emulation is state on the session, so a second
    session's "no latency" does not reliably undo the first's, and a mid-query shot
    would leave every state captured after it loading over a slow link (which is how
    the graph came up missing on the state that follows it).
    """
    session = _SESSIONS.get(id(page))
    if session is None:
        session = page.context.new_cdp_session(page)
        session.send("Network.enable")
        _SESSIONS[id(page)] = session
    session.send("Network.emulateNetworkConditions", {
        "offline": False, "latency": latency_ms,
        "downloadThroughput": -1, "uploadThroughput": -1,
    })


def field(page: Page, label: str):
    """The visible dropdown carrying ``label``.

    Gradio puts the label on a single-select's ``aria-label`` but not on a
    multiselect's, so the fallback goes through the field's own label span. Every
    tab's controls stay in the DOM, so the visible filter is what keeps a selector
    from matching the same-named control on a hidden tab.

    Raises on a label the app no longer carries rather than handing back a locator
    that matches nothing: #156's copy sweep renamed three of the labels driven here,
    and the symptom was a 30s Playwright timeout on the click that came next, which
    names the selector but never the state that was being captured."""
    by_aria = page.locator(f'input[aria-label="{label}"]').locator("visible=true")
    if by_aria.count():
        return by_aria.first
    by_span = page.locator("div.container, label.container").filter(
        has=page.locator(f'span[data-testid="block-info"]:text-is("{label}")')
    ).locator("input").locator("visible=true")
    if not by_span.count():
        raise SystemExit(f"No visible control labelled {label!r}")
    return by_span.first


def pick(page: Page, label: str, text: str) -> None:
    """Type into a dropdown and take the option whose label starts with what was typed.

    Prefix, not equality: the archetype timeline's options carry an event count
    (``Grixis (85 events)``) and a duplicate display name carries its key, so the
    caller names the entity and this takes the row it opens."""
    box = field(page, label)
    box.click()
    box.fill(text)
    wait(page, 500)
    options = page.locator("ul.options li").locator("visible=true")
    for i in range(options.count()):
        option = options.nth(i)
        if option.inner_text().strip().startswith(text):
            option.click()
            wait(page, 400)
            return
    raise SystemExit(f"No option starting with {text!r} for {label!r}")


def draw(page: Page, settle: int = 6000) -> None:
    """Press the visible Draw, wait for the answer, and let it settle.

    Waited for rather than slept through: a view's results are hidden until its
    callback returns, so their appearing is the signal the query is done, and a fixed
    sleep is a guess that comes up short exactly when the machine is busy. The settle
    on top is for what happens after the DOM lands: a graph stabilising its physics
    layout, and Plotly laying out on its own frame.
    """
    page.get_by_role("button", name="Draw", exact=True).locator("visible=true").first.click()
    page.locator(".results-stack, .insight-card").locator("visible=true").first.wait_for(
        timeout=60_000)
    wait(page, settle)


def click_a_node(page: Page) -> None:
    """Click a node inside the pyvis iframe, so the details panel is populated.

    The graph is a canvas, so there is no element to target: the node's position is
    asked of vis.js and converted to document coordinates, and the click is a real
    one at that point, which is what fires the ``selectNode`` handler the panel
    listens on (a programmatic ``selectNodes`` would not).

    Both locators are filtered to the *visible* iframe, and have to be. Every tab's
    graph stays in the DOM, so once the gems tab started drawing one of its own on
    open (#176) the first iframe on the page stopped being the one on screen: the
    point was read off the visible graph and the click was sent to a hidden one, whose
    canvas is 0x0, which Playwright waits out as a 30s "element is not visible" rather
    than reporting a wrong frame.
    """
    frame = page.frame_locator("iframe >> visible=true").first
    handle = page.locator("iframe").locator("visible=true").first
    point = handle.element_handle().content_frame().evaluate(
        """() => {
             // A deck, so the panel shows its Moxfield link as well as its fields;
             // failing that (a graph of cards and archetypes holds no deck), the most
             // connected node, which is the query's own seed in the middle.
             const ids = network.body.data.nodes.getIds();
             const degree = id => network.getConnectedNodes(id).length;
             const deck = ids.find(id => NODE_META[id] && NODE_META[id].moxfield);
             const id = deck || ids.reduce((a, b) => (degree(b) > degree(a) ? b : a));
             return network.canvasToDOM(network.getPositions([id])[id]);
           }"""
    )
    # Clicked through the frame at a canvas-relative point, so Playwright scrolls the
    # graph into view itself and the coordinate needs no page-offset arithmetic.
    frame.locator("#mynetwork canvas").click(position=point)
    wait(page, 800)
    # The panel opens on a prompt to click a node; if that is still what it says, the
    # click missed and the shot would show the un-clicked state as if it were the panel.
    if "Click a node" in frame.locator("#node-details").inner_text():
        raise SystemExit("the node click selected nothing: the details panel is unchanged")


@dataclass
class Shot:
    name: str
    caption: str
    steps: object  # Callable[[Page], None]


def recipes() -> list[Shot]:
    """Every tab and state, in the order the matrix reads."""
    def meta_default(page):
        pass

    def meta_focus(page):
        pick(page, "Or focus on specific archetypes", ARCHETYPE)
        pick(page, "Or focus on specific archetypes", ARCHETYPE_B)
        wait(page, 2500)

    def archetypes_landscape(page):
        tab(page, "Archetypes")
        wait(page, 2000)

    def archetypes_timeline(page):
        tab(page, "Archetypes")
        pick(page, "Archetype", ARCHETYPE)
        wait(page, 3000)

    def archetypes_head_to_head(page):
        tab(page, "Archetypes")
        pick(page, "Archetype", ARCHETYPE)
        pick(page, "Second archetype (optional)", ARCHETYPE_B)
        wait(page, 3500)

    def archetypes_refused(page):
        tab(page, "Archetypes")
        pick(page, "Archetype", ARCHETYPE_LONE)
        pick(page, "Second archetype (optional)", ARCHETYPE_LONE_B)
        wait(page, 2500)

    def cards_nothing_picked(page):
        tab(page, "Cards")

    def cards_overview(page):
        tab(page, "Cards")
        pick(page, "Card", CARD)
        draw(page)

    def cards_cooccurrence(page):
        tab(page, "Cards")
        pick(page, "Card", CARD)
        page.locator('input[aria-label="View"]').locator("visible=true").first.click()
        page.locator("ul.options li").locator("visible=true").filter(
            has_text="Co-occurrence").first.click()
        wait(page, 600)
        pick(page, "Second card (optional)", CARD_B)
        draw(page)

    def gems(page):
        # Nothing to pick and nothing to draw since #184: the tab renders the whole
        # format's gems on open, so the shot is the tab.
        tab(page, "Hidden gems")
        wait(page, 1500)

    def pilots_nothing_picked(page):
        tab(page, "Pilots")

    def pilots_running(page):
        tab(page, "Pilots")
        pick(page, "Pilot", PILOT)
        # Held open with a second of emulated latency, since the shot is of the app
        # mid-query and a local query answers faster than a screenshot can be taken.
        # The latency is the reader's connection, not the app: the state photographed
        # is the one a phone on a slow network actually sits in.
        throttle(page, 1000)
        page.get_by_role("button", name="Draw", exact=True).locator(
            "visible=true").first.click()
        wait(page, 900)

    def pilots_overview(page):
        tab(page, "Pilots")
        pick(page, "Pilot", PILOT)
        draw(page, settle=8000)

    def pilots_node_details(page):
        pilots_overview(page)
        click_a_node(page)

    def pilots_thin_history(page):
        tab(page, "Pilots")
        pick(page, "Pilot", PILOT_THIN)
        draw(page)

    def pilots_head_to_head(page):
        tab(page, "Pilots")
        pick(page, "Pilot", PILOT_RIVAL)
        page.locator('input[aria-label="View"]').locator("visible=true").first.click()
        page.locator("ul.options li").locator("visible=true").filter(
            has_text="Head-to-head").first.click()
        wait(page, 600)
        pick(page, "Second pilot (required)", PILOT_B)
        draw(page, settle=8000)

    def pilots_same_pilot(page):
        tab(page, "Pilots")
        pick(page, "Pilot", PILOT_RIVAL)
        page.locator('input[aria-label="View"]').locator("visible=true").first.click()
        page.locator("ul.options li").locator("visible=true").filter(
            has_text="Head-to-head").first.click()
        wait(page, 600)
        pick(page, "Second pilot (required)", PILOT_RIVAL)
        draw(page, settle=3000)

    def pilots_never_met(page):
        tab(page, "Pilots")
        pick(page, "Pilot", PILOT_RIVAL)
        page.locator('input[aria-label="View"]').locator("visible=true").first.click()
        page.locator("ul.options li").locator("visible=true").filter(
            has_text="Head-to-head").first.click()
        wait(page, 600)
        pick(page, "Second pilot (required)", PILOT_STRANGER)
        draw(page, settle=6000)

    def faq(page):
        tab(page, "FAQ")
        wait(page, 600)

    return [
        Shot("meta-default", "Meta, cold start: the app opens on a drawn chart", meta_default),
        Shot("meta-focus", "Meta with two archetypes focused", meta_focus),
        Shot("archetypes-landscape", "Archetypes, the metagame landscape", archetypes_landscape),
        Shot("archetypes-timeline", "Archetypes, one archetype's finishes over time", archetypes_timeline),
        Shot("archetypes-head-to-head", "Archetypes, two archetypes compared", archetypes_head_to_head),
        Shot("archetypes-refused", "Archetypes, a pair with no shared ranked event (refusal)", archetypes_refused),
        Shot("cards-nothing-picked", "Cards, nothing picked yet", cards_nothing_picked),
        Shot("cards-overview", "Cards, a card's usage graph and adoption trend", cards_overview),
        Shot("cards-cooccurrence", "Cards, co-occurrence of a pair", cards_cooccurrence),
        Shot("gems-drawn", "Hidden gems, the whole format's gems, drawn on open", gems),
        Shot("pilots-nothing-picked", "Pilots, nothing picked yet", pilots_nothing_picked),
        Shot("pilots-running", "Pilots, mid-query: progress while a Draw runs", pilots_running),
        Shot("pilots-overview", "Pilots, a pilot's neighbourhood, affinity and performance", pilots_overview),
        Shot("pilots-node-details", "Pilots, a deck node clicked: the details panel and its Moxfield link", pilots_node_details),
        Shot("pilots-thin-history", "Pilots, a pilot with too little history to average (refusal)", pilots_thin_history),
        Shot("pilots-head-to-head", "Pilots, a rivalry drawn", pilots_head_to_head),
        Shot("pilots-same-pilot", "Pilots, the same pilot picked twice", pilots_same_pilot),
        Shot("pilots-never-met", "Pilots, a pair who never met (refusal)", pilots_never_met),
        Shot("faq", "FAQ", faq),
    ]


def forced_recipes() -> list[Shot]:
    """The one state the shipped corpus cannot reach from the controls."""
    def too_large(page):
        tab(page, "Pilots")
        pick(page, "Pilot", PILOT)
        draw(page, settle=3000)

    return [
        Shot("forced-too-large", "Too large to draw, with the draw limit lowered to 20 nodes", too_large),
    ]


# Walks every painted text node and measures it against the background it actually
# sits on: the composited stack of ancestor surfaces, not a token looked up. SVG text
# (the Plotly chart chrome) carries its colour on `fill`, so it is read there. Returns
# one row per text node with its ratio and the AA floor that applies to it, so the
# report can state what passed as well as what failed.
_AUDIT_JS = r"""
() => {
  const rgb = s => { const m = String(s).match(/[\d.]+/g); return m ? m.map(Number) : null; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
                     return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const over = (fg, bg) => { const a = fg.length > 3 ? fg[3] : 1;
                             return [0, 1, 2].map(i => fg[i] * a + bg[i] * (1 - a)); };
  const ratio = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
                            return (hi + 0.05) / (lo + 0.05); };
  const hex = c => '#' + c.map(v => Math.round(v).toString(16).padStart(2, '0')).join('');

  function background(el) {
    // Every surface from the element up to the first opaque one, composited back down.
    const layers = [];
    let base = [255, 255, 255];
    for (let n = el; n; n = n.parentElement) {
      const c = rgb(getComputedStyle(n).backgroundColor);
      if (!c) continue;
      const a = c.length > 3 ? c[3] : 1;
      if (a >= 1) { base = c.slice(0, 3); break; }
      if (a > 0) layers.push(c);
    }
    return layers.reduceRight((bg, layer) => over(layer, bg), base);
  }

  function opacity(el) {
    let o = 1;
    for (let n = el; n; n = n.parentElement) o *= parseFloat(getComputedStyle(n).opacity || '1');
    return o;
  }

  const rows = [];
  for (const el of document.querySelectorAll('*')) {
    const text = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').trim();
    if (!text) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    const box = el.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) continue;
    const alpha = opacity(el);
    if (alpha < 0.05) continue;
    const svg = el.namespaceURI === 'http://www.w3.org/2000/svg';
    const colour = rgb(svg ? style.fill : style.color);
    if (!colour) continue;
    const bg = background(el);
    // The element's own inherited opacity fades the ink into its background.
    const ink = over([...colour.slice(0, 3), (colour.length > 3 ? colour[3] : 1) * alpha], bg);
    const size = parseFloat(style.fontSize);
    const weight = parseInt(style.fontWeight, 10) || 400;
    // WCAG AA: 4.5:1 for body text, 3:1 for large text (>=24px, or >=18.66px bold).
    const floor = (size >= 24 || (size >= 18.66 && weight >= 700)) ? 3.0 : 4.5;
    rows.push({
      text: text.slice(0, 60), tag: el.tagName.toLowerCase(),
      cls: (typeof el.className === 'string' ? el.className : '').slice(0, 60),
      size, weight, fg: hex(ink), bg: hex(bg),
      ratio: Math.round(ratio(ink, bg) * 100) / 100, floor,
    });
  }
  return rows;
}
"""


def audit(page: Page, state: str, width: str) -> list[dict]:
    """Measure every text node on the page and inside the graph iframe."""
    rows = []
    for frame in page.frames:
        try:
            found = frame.evaluate(_AUDIT_JS)
        except Exception:  # a frame that navigated mid-audit has nothing to measure
            continue
        for row in found:
            rows.append({**row, "state": state, "width": width,
                         "frame": "graph" if frame.parent_frame else "page"})
    return rows


def report(out: Path) -> str:
    """The contrast pass as markdown, written from every measurement in ``out``.

    Generated rather than transcribed: the numbers in the write-up are the ones the
    browser reported, and re-running the pass rewrites them. Groups the measurements
    by the ink-on-ground pair actually painted, since that is what a fix would change,
    and lists every pair below its AA floor with a state it appears in.
    """
    rows = [row for file in sorted(out.glob("contrast*.json"))
            for row in json.loads(file.read_text())]
    if not rows:
        return ""
    pairs: dict[tuple, dict] = {}
    for row in rows:
        key = (row["fg"], row["bg"], row["floor"])
        seen = pairs.setdefault(key, {"n": 0, "min": row, "example": row})
        seen["n"] += 1
        if row["ratio"] < seen["min"]["ratio"]:
            seen["min"] = row
    failures = {k: v for k, v in pairs.items() if v["min"]["ratio"] < k[2]}
    states = {(r["state"], r["width"]) for r in rows}

    lines = [
        f"{len(rows):,} text nodes measured over {len(states)} state/width visits, "
        f"{sum(1 for r in rows if r['ratio'] < r['floor']):,} below their WCAG AA floor.",
        "",
        "| ink | ground | ratio | floor | nodes | example |",
        "|---|---|---|---|---|---|",
    ]
    for (fg, bg, floor), seen in sorted(pairs.items(), key=lambda kv: kv[1]["min"]["ratio"]):
        mark = "**" if seen["min"]["ratio"] < floor else ""
        example = seen["example"]
        lines.append(
            f"| `{fg}` | `{bg}` | {mark}{seen['min']['ratio']:.2f}{mark} | {floor} | "
            f"{seen['n']} | {example['size']:g}px "
            f"{example['tag']} \"{example['text'][:28]}\" ({example['state']}) |"
        )
    lines += ["", f"{len(failures)} ink-on-ground pair below AA (in bold above)."
              if failures else "Every ink-on-ground pair clears AA."]
    return "\n".join(lines) + "\n"


def gallery(out: Path) -> str:
    """The matrix as one local page: each state's phone and desktop shot side by side.

    Sign-off is a person looking at every state, and that is 42 files in two folders.
    The page puts each state on one row under its own caption, in matrix order, so the
    review is a scroll. Local and gitignored, like the shots it links.
    """
    rows = []
    for shot in recipes() + forced_recipes():
        pair = "".join(
            f'<figure><figcaption>{width}</figcaption>'
            f'<a href="{width}/{shot.name}.png"><img src="{width}/{shot.name}.png"></a>'
            "</figure>"
            for width in VIEWPORTS
            if (out / width / f"{shot.name}.png").exists()
        )
        if pair:
            rows.append(f"<section><h2>{shot.name}</h2><p>{shot.caption}</p>"
                        f'<div class="pair">{pair}</div></section>')
    return f"""<!doctype html>
<meta charset="utf-8"><title>v1 acceptance matrix</title>
<style>
  body {{ background: #131110; color: #f2ede6; font: 15px/1.5 system-ui, sans-serif;
         margin: 0 auto; padding: 2rem; max-width: 1400px; }}
  h1 {{ font-size: 28px; }}
  h2 {{ font-size: 20px; margin-bottom: 0.2rem; }}
  section {{ border-top: 1px solid #37312b; padding-top: 1.5rem; margin-top: 2rem; }}
  p {{ color: #b4aca2; margin-top: 0; }}
  .pair {{ display: flex; gap: 1.5rem; align-items: flex-start; }}
  figure {{ margin: 0; }}
  figcaption {{ color: #8a8178; font-size: 12px; letter-spacing: 0.06em;
                text-transform: uppercase; margin-bottom: 0.4rem; }}
  img {{ max-height: 900px; width: auto; border: 1px solid #37312b; border-radius: 8px; }}
</style>
<h1>v1 acceptance matrix (#118)</h1>
<p>Every tab and state at phone (390) and desktop (1440) width, in the shipped dark
theme. Click a shot for the full-size image.</p>
{"".join(rows)}
"""


def shrink(image: Path) -> None:
    """Quantise a shot to a 256-colour palette in place.

    A screenshot of this app is flat dark surfaces, type and line art, so a palette
    holds it with no visible loss and about a fifth of the bytes. That is what makes
    a 46-shot matrix something the repo can carry beside the code it is evidence for.
    """
    from PIL import Image

    with Image.open(image) as shot:
        shot.convert("RGB").quantize(colors=256).save(image, optimize=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("docs/design/acceptance/v1"))
    parser.add_argument("--forced", action="store_true",
                        help="lower the draw limit, to reach the refusal the shipped "
                             "corpus cannot")
    parser.add_argument("--only", help="capture only these states (comma-separated)")
    parser.add_argument("--gallery", action="store_true",
                        help="rebuild the review page from the shots already captured")
    args = parser.parse_args()

    if args.gallery:
        (args.out / "gallery.html").write_text(gallery(args.out))
        return

    from graph7ph import app as app_module
    from graph7ph import explore
    from graph7ph.db import artifact_path
    from graph7ph.serve import APP_KWARGS

    shots = forced_recipes() if args.forced else recipes()
    if args.only:
        wanted = args.only.split(",")
        shots = [s for s in shots if s.name in wanted]

    demo = app_module.build_app(artifact_path())
    if args.forced:
        app_module.assess = lambda subgraph: explore.assess(subgraph, threshold=20)

    port = free_port()
    demo.launch(server_port=port, app_kwargs=APP_KWARGS, quiet=True,
                prevent_thread_lock=True, share=False, inbrowser=False)
    url = f"http://127.0.0.1:{port}/?__theme=dark"

    measurements: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width, options in VIEWPORTS.items():
            directory = args.out / width
            directory.mkdir(parents=True, exist_ok=True)
            context = browser.new_context(**options)
            page = context.new_page()
            for shot in shots:
                throttle(page, 0)  # release any latency the state before emulated
                page.goto(url, wait_until="networkidle")
                # The Meta chart is drawn at build time and lays out on its own frame.
                page.wait_for_timeout(3500)
                shot.steps(page)
                image = directory / f"{shot.name}.png"
                page.screenshot(path=image, full_page=True)
                shrink(image)
                measurements += audit(page, shot.name, width)
                print(f"  {width}/{shot.name}", flush=True)
            context.close()
        browser.close()

    args.out.mkdir(parents=True, exist_ok=True)
    name = "contrast-forced.json" if args.forced else "contrast.json"
    (args.out / name).write_text(json.dumps(measurements, indent=1) + "\n")
    (args.out / "contrast.md").write_text(report(args.out))
    (args.out / "gallery.html").write_text(gallery(args.out))
    failures = [r for r in measurements if r["ratio"] < r["floor"]]
    print(f"{len(measurements)} text nodes measured, {len(failures)} below AA")


if __name__ == "__main__":
    main()
