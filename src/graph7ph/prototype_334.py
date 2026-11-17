"""PROTOTYPE for issue #334. Throwaway: no tests, never merged into main.

The question: should Metagame's lead card, "who is played", be a ranked list or stay
the line chart? Three variants on the real app and the real record, switched by
``?variant=`` and by the pill at the foot of the page (arrow keys work too):

  today  Today            the chart as it ships
  a      Ranked list      one row per archetype: this year's share as a bar, the
                          change from last year, and the four years as columns. The
                          focus picker is gone
  b      Restyled chart   the same chart under the caption rule, with its legend
                          taken out of the figure and set under it as chips, so it
                          wraps at any width. The focus picker keeps one clear button

Run from the worktree, against the main checkout's built graph::

    GRADIO_SERVER_PORT=7934 uv run python -m graph7ph.prototype_334

All three are on the page at once and the stylesheet shows one, so a switch costs
no redraw. The Metagame tab in ``app.py`` carries the few lines that hand each card
its three forms, every one marked ``PROTOTYPE #334``.
"""

import argparse
import html
from pathlib import Path

import plotly.graph_objects as pgo

from graph7ph import numfmt, parts
from graph7ph.theme import FIGURE_BLUE

# What a cut holds, in the caption's words.
_HELD = {0.25: "a quarter", 0.50: "half", 0.75: "three quarters"}

_FAQ = "faq-meta"


def _title(text: str) -> str:
    return (f"<div class='titled'><div class='t-result-title'>{html.escape(text)}</div>"
            f"<a class='measured' href='#{_FAQ}'>How this is measured</a></div>")


def _cells(series, tags):
    """Each drawn archetype's cells by year, in the order given."""
    wanted = set(tags)
    by_tag: dict[str, dict] = {}
    for cell in series.cells:
        if cell.tag in wanted:
            by_tag.setdefault(cell.tag, {})[cell.year] = cell
    return by_tag


# --- a: the ranked list --------------------------------------------------------------


def ranked_list(series, tags, cut: float, latest: int, pages) -> str:
    """The card as a ranked list: its head, its caption and one row per archetype."""
    by_tag = _cells(series, tags)
    years = sorted({cell.year for cell in series.cells})
    before = latest - 1
    # One scale for every history, whatever the cut: the largest share any archetype
    # took in any year. And one for the bars: the leader's share this year.
    scale = max(cell.share for cell in series.cells)
    leader = by_tag[tags[0]][latest].share

    def history(cells) -> parts.Markup:
        said = ", ".join(f"{year} {numfmt.share(cells[year].share)}" for year in years)
        columns = "".join(
            f"<span class='p334-col{' now' if year == latest else ''}' "
            f"style='--v:{cells[year].share:.5f}' "
            f"title='{year}: {numfmt.share(cells[year].share)}, "
            f"{numfmt.count_of(cells[year].n, cells[year].year_total, 'decks')}'></span>"
            for year in years
        )
        return parts.Markup(
            f"<span class='p334-hist' role='img' aria-label='{said}'>{columns}</span>"
        )

    def row(rank: int, tag: str) -> list[str]:
        cells = by_tag[tag]
        now, was = cells[latest], cells[before]
        return [
            str(rank),
            pages.link("archetype", tag, now.archetype),
            parts.Markup(
                f"<span class='p334-track' aria-hidden='true' style='--v:{now.share:.5f}'>"
                "<span class='p334-bar'></span></span>"
                f"<span class='p334-share'>{numfmt.share(now.share)}</span>"
                f"<span class='sub'>{now.n:,} decks</span>"
            ),
            numfmt.count_of(now.n, now.year_total),
            parts.Markup(
                f"{numfmt.share_change(now.share, was.share)}"
                f"<span class='p334-from'>from {numfmt.share(was.share)}</span>"
            ),
            history(cells),
        ]

    table = parts.table(
        [
            parts.Column("#", numeric=True, quiet=True),
            parts.Column("Archetype"),
            parts.Column(f"Share of {latest} decks", numeric=True),
            parts.Column("Decks", numeric=True, quiet=True, wide_only=True),
            parts.Column(f"Change from {before}", numeric=True),
            parts.Column(parts.Markup(
                "<span class='p334-ends'>" + "".join(
                    f"<span><span class='p334-century'>{str(year)[:2]}</span>{str(year)[2:]}</span>"
                    for year in (years[0], years[-1])
                ) + "</span>"
            ), numeric=True),
        ],
        [row(rank, tag) for rank, tag in enumerate(tags, start=1)],
    )
    caption = parts.caption(
        f"The {{}} most played archetypes of {latest}, which together hold {_HELD[cut]} "
        f"of its decks. The columns are each one's share in every year on record, "
        f"on one scale.",
        str(len(tags)),
    )
    return (
        f"{_title('Meta share')}{caption}"
        f"<div class='p334-list' style='--leader:{leader:.5f};--scale:{scale:.5f}'>{table}</div>"
    )


# --- b: the restyled chart -----------------------------------------------------------

_PLOT_HEIGHT = 460  # the figure without its legend; it was 640 with it


def restyled_head(count: int, cut: float, latest: int) -> str:
    return _title("Meta share over time") + parts.caption(
        f"The {{}} most played archetypes of {latest}, which together hold {_HELD[cut]} "
        "of its decks. Click a name under the chart to raise or fade its line.",
        str(count),
    )


def picked_head() -> str:
    return _title("Selected archetypes") + parts.caption(
        "Click a name under the chart to raise or fade its line."
    )


def restyled_figure(fig: pgo.Figure) -> pgo.Figure:
    """Today's figure with the legend left out: the chips under it stand in."""
    fig.update_layout(showlegend=False, height=_PLOT_HEIGHT)
    return fig


def chips(series, tags, hues: dict[str, str], raised: int) -> str:
    """The legend as markup: a chip per archetype, in the hue its line is drawn in.

    Chip ``i`` answers for trace ``len(tags) + i``, the full-strength twin of the
    faded line (``app._trend_figure`` adds the faded pass first, then the raised).
    """
    by_tag = _cells(series, tags)
    items = "".join(
        f"<button type='button' class='p334-chip' data-trace='{len(tags) + i}' "
        f"aria-pressed='{'true' if i < raised else 'false'}' style='--hue:{hues[tag]}'>"
        f"<span class='p334-dot'></span>{html.escape(next(iter(by_tag[tag].values())).archetype)}"
        "</button>"
        for i, tag in enumerate(tags)
    )
    return f"<div class='p334-chips'>{items}</div>"


# --- what is bolted onto the page ----------------------------------------------------

_PAGE_CSS = f"""
/* One variant on show. A chart that is not on show keeps its width and gives up its
   height, so the library draws it at the size it will be shown at. */
html[data-proto-variant="today"] .p334-a, html[data-proto-variant="today"] .p334-b,
html[data-proto-variant="a"] .p334-today, html[data-proto-variant="a"] .p334-b,
html[data-proto-variant="b"] .p334-today, html[data-proto-variant="b"] .p334-a {{
  display: none !important;
}}
html[data-proto-variant="today"] .p334-plot-b, html[data-proto-variant="a"] .p334-plot-b,
html[data-proto-variant="a"] .p334-plot-today, html[data-proto-variant="b"] .p334-plot-today {{
  height: 0 !important; min-height: 0 !important; overflow: hidden !important;
  visibility: hidden !important; padding: 0 !important; margin: 0 !important;
  border: 0 !important;
}}
/* a: the focus picker goes, and the card it drew. */
html[data-proto-variant="a"] .control-panel .row.clearable:has(#meta-focus),
html[data-proto-variant="a"] #meta-focus-result {{ display: none !important; }}
/* b: one clear control, the app's. */
html[data-proto-variant="b"] #meta-focus .remove-all,
html[data-proto-variant="b"] #meta-focus .token-remove.remove-all {{ display: none !important; }}

/* a: the list. The bar's track is the leader's share, and every history is on one
   scale. Blue is this year, in the bar and in the last column. */
.p334-list {{ --track: clamp(4rem, 26vw, 24rem); margin-top: 0.6rem; }}
.p334-list .p334-track {{
  display: inline-block; vertical-align: middle; position: relative;
  width: var(--track); height: 8px; margin-right: 0.7rem;
  border-radius: 2px; background: var(--border);
}}
.p334-list .p334-bar {{
  position: absolute; left: 0; top: 0; bottom: 0; border-radius: 2px;
  width: calc(var(--v) / var(--leader) * 100%); background: {FIGURE_BLUE};
}}
.p334-list .p334-share {{ display: inline-block; min-width: 3.4rem; color: var(--text); }}
.p334-list .p334-from {{
  display: inline-block; min-width: 6.2rem; margin-left: 0.6rem; text-align: left;
  color: var(--text-mute);
}}
.p334-list .p334-hist, .p334-list .p334-ends {{ display: inline-flex; width: 6rem; }}
.p334-list .p334-ends {{ justify-content: space-between; letter-spacing: 0; }}
.p334-list .p334-ends, .p334-list .p334-ends span {{ color: inherit; }}
.p334-list .p334-hist {{
  height: 28px; align-items: flex-end; gap: 4px; vertical-align: middle;
  border-bottom: 1px solid var(--border);
}}
.p334-list .p334-col {{
  flex: 1 1 0; border-radius: 1px 1px 0 0; background: var(--text-mute);
  height: calc(var(--v) / var(--scale) * 100%);
}}
.p334-list .p334-col.now {{ background: {FIGURE_BLUE}; }}
.p334-list td {{ vertical-align: middle; }}
@media (max-width: 640px) {{
  .p334-list .p334-track {{ display: block; width: 100%; margin: 0 0 4px; }}
  .p334-list .p334-share {{ min-width: 0; }}
  .p334-list .p334-from {{
    display: block; min-width: 0; margin: 0; text-align: right; font-size: 12px;
  }}
  .p334-list .p334-hist, .p334-list .p334-ends {{ width: 2.75rem; }}
  .p334-list .p334-hist {{ gap: 2px; height: 22px; }}
  .p334-list .p334-century {{ display: none; }}
}}

/* b: the legend as chips. Raised is the hue filled and the name in the main ink;
   faded is a ring and the name set back. */
.p334-chips {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.5rem; }}
.p334-chips .p334-chip {{
  display: inline-flex; align-items: center; gap: 0.45rem; cursor: pointer;
  padding: 0.3rem 0.65rem; border: 1px solid var(--border); border-radius: 999px;
  background: transparent; color: var(--text-dim); font: inherit; font-size: 13px;
  line-height: 1.2; transition: color 120ms ease, border-color 120ms ease;
}}
.p334-chips .p334-chip:hover {{ border-color: var(--text-mute); color: var(--text); }}
.p334-chips .p334-dot {{
  width: 10px; height: 10px; border-radius: 50%; box-sizing: border-box;
  border: 2px solid var(--hue); background: transparent;
}}
.p334-chips .p334-chip[aria-pressed="true"] {{ color: var(--text); border-color: var(--text-mute); }}
.p334-chips .p334-chip[aria-pressed="true"] .p334-dot {{ background: var(--hue); }}

#proto-bar {{
  position: fixed; left: 50%; bottom: 14px; transform: translateX(-50%); z-index: 99999;
  display: flex; align-items: center; gap: 0.25rem; padding: 0.3rem 0.35rem;
  border-radius: 999px; background: #fdf6e3; color: #111;
  font: 600 13px/1 system-ui, sans-serif; box-shadow: 0 8px 28px rgba(0, 0, 0, 0.6);
}}
#proto-bar button {{
  width: 28px; height: 28px; border: 0; border-radius: 999px; cursor: pointer;
  background: #111; color: #fdf6e3; font: inherit;
}}
#proto-bar span {{ padding: 0 0.6rem; white-space: nowrap; }}
"""

_PAGE_JS = """
() => {
  const VARIANTS = [["today", "Today"], ["a", "Ranked list"], ["b", "Restyled chart"]];
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
    label.textContent = "Prototype #334 \\u00b7 " + name;
    window.dispatchEvent(new Event("resize"));
  }
  function step(by) { at = (at + by + VARIANTS.length) % VARIANTS.length; apply(); }
  prev.addEventListener("click", () => step(-1));
  next.addEventListener("click", () => step(1));
  document.addEventListener("keydown", (event) => {
    if (event.target.closest("input, textarea, select, [contenteditable], [role=tab], [role=radio]")) return;
    if (event.key === "ArrowLeft") step(-1);
    if (event.key === "ArrowRight") step(1);
  });
  apply();

  // b: a chip raises or fades its line. The chart is the one in the chip's own card.
  const plotOf = (el) => el.closest(".insight-card").querySelector(".p334-plot-b .js-plotly-plot");
  function sync(chipsEl) {
    const plot = plotOf(chipsEl);
    if (!plot || !plot.data) return;
    chipsEl.querySelectorAll(".p334-chip").forEach((chip) => {
      const trace = plot.data[Number(chip.dataset.trace)];
      if (trace) chip.setAttribute("aria-pressed", String(trace.visible === true));
    });
  }
  document.addEventListener("click", (event) => {
    const chip = event.target.closest(".p334-chip");
    if (!chip) return;
    const plot = plotOf(chip), at = Number(chip.dataset.trace);
    if (!plot || !plot.data || !plot.data[at]) return;
    const raise = plot.data[at].visible !== true;
    window.Plotly.restyle(plot, {visible: raise ? true : "legendonly"}, [at])
      .then(() => sync(chip.parentElement));
  });
}
"""


def main() -> None:
    from graph7ph import app

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=Path("../graph-7ph/data/graph"))
    args = parser.parse_args()

    kwargs = app.launch_kwargs()
    kwargs["css"] += _PAGE_CSS
    kwargs["js"] = _PAGE_JS
    app.build_app(args.db).launch(**kwargs)


if __name__ == "__main__":
    main()
