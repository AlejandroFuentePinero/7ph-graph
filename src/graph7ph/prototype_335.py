"""PROTOTYPE for issue #335. Throwaway: no tests, never merged into main.

The question: what should two archetypes' (or two pilots') finishes over their shared
events look like, so the chart still reads where events are dense? Three forms on the
real app and the real record, switched by ``?variant=`` and by the pill at the foot of
the page (arrow keys work too):

  today  Today          the chart as it ships: two dashed lines on a date axis, a
                        band between them, the tall tinted range control under it
  a      Gap per event  one bar per shared event, the gap between the two finishes,
                        above the zero line where the first finished better and below
                        it where the second did, in the leader's colour. Events are
                        spaced evenly in date order, so there is no range control
  b      Lines, spaced  today's two lines and band, with the events spaced evenly in
                        date order and the marks sized to how many there are

The single timeline (one archetype, no second) is not a variant: it is the ticket's
direct change, shown under ``a`` and ``b`` both, against today's under ``today``.

Run from the worktree, against its own built graph::

    GRADIO_SERVER_PORT=7935 uv run python -m graph7ph.prototype_335

All three are on the page at once and the stylesheet shows one, so a switch costs no
redraw. ``app.py`` carries the few lines that hand each card its three forms, every
one marked ``PROTOTYPE #335``.
"""

import argparse
from pathlib import Path

import plotly.graph_objects as pgo

from graph7ph import numfmt, palette, parts

_BOUND_LEGEND_A = (
    "An open bar is a meeting where one side has no finish published, so the gap "
    "is the smallest it could have been"
)

# A slim neutral range control: this many pixels tall, no label, no tint.
_SLIM_BAND = 12

# How many of the latest events variant b opens on at phone width.
PHONE_WINDOW = 30


def _app():
    # Imported late: `app` imports this module at load.
    from graph7ph import app
    return app


def _settled(cells):
    """The meetings the record settles, oldest first: what the caption counts."""
    return sorted(
        (c for c in cells if all(norm is not None for norm, _ in c.drawn())),
        key=lambda c: c.date,
    )


def _year_axis(fig: pgo.Figure, cells, title: str) -> None:
    """Evenly spaced events on a linear axis, each year named at the middle of its run.

    A year whose run is under a tenth of the axis is marked by its rule alone: its
    name would run into its neighbour's at phone width.
    """
    app = _app()
    years: dict[int, list[int]] = {}
    for at, cell in enumerate(cells):
        years.setdefault(cell.date.year, []).append(at)
    named = {year: run for year, run in years.items() if len(run) >= len(cells) / 10}
    fig.update_xaxes(
        title=title, type="linear", categoryorder=None, autorange=False,
        range=[-0.75, len(cells) - 0.25],
        tickmode="array",
        tickvals=[(run[0] + run[-1]) / 2 for run in named.values()],
        ticktext=[str(year) for year in named],
        ticks="", showgrid=False,
    )
    for year, run in list(years.items())[1:]:
        fig.add_vline(x=run[0] - 0.5, line=dict(color=app._GRID, width=1))


def archetype_readout(cell, side: int, drawn) -> list:
    app = _app()
    norm, is_bound = drawn
    decks = (cell.decks_a, cell.decks_b)[side]
    return app._bounded_readout(norm, is_bound, f"{decks} decks",
                                imputed=cell.field_imputed is not None)


def pilot_readout(cell, side: int, drawn) -> list:
    app = _app()
    placement, norm, placement_rule, norm_rule = (
        (cell.placement_a, cell.norm_a, cell.placement_imputed_a, cell.norm_imputed_a),
        (cell.placement_b, cell.norm_b, cell.placement_imputed_b, cell.norm_imputed_b),
    )[side]
    drawn_norm, is_bound = drawn
    if is_bound or norm is None:
        return app._bounded_readout(drawn_norm, is_bound,
                                    imputed=cell.field_imputed is not None)
    return [
        numfmt.score(1 - norm, imputed=norm_rule is not None),
        numfmt.count_of(placement, cell.field_size,
                        count_imputed=placement_rule is not None,
                        total_imputed=cell.field_imputed is not None),
    ]


# --- a: the gap per event ------------------------------------------------------------


def gap_figure(name_a: str, name_b: str, series, readout) -> pgo.Figure:
    """One bar per settled meeting: how much better one side finished than the other."""
    app = _app()
    cells = _settled(series.cells)
    colour_a, colour_b = palette.CATEGORICAL[0], palette.CATEGORICAL[1]
    fig = pgo.Figure()

    xs, ys, fills, edges, widths, custom = [], [], [], [], [], []
    level_x, level_custom = [], []
    for at, cell in enumerate(cells):
        (norm_a, bound_a), (norm_b, bound_b) = cell.drawn()
        gap = norm_b - norm_a  # the finish flipped to a score, a's less b's
        said = [f"{cell.date:%d %b %Y}",
                *readout(cell, 0, (norm_a, bound_a)), *readout(cell, 1, (norm_b, bound_b))]
        if gap == 0:
            level_x.append(at)
            level_custom.append([*said, "Level"])
            continue
        colour = colour_a if gap > 0 else colour_b
        bounded = bound_a or bound_b
        xs.append(at)
        ys.append(gap)
        fills.append("rgba(0,0,0,0)" if bounded else colour)
        edges.append(colour)
        widths.append(1.5 if bounded else 0)
        custom.append([
            *said,
            f"{name_a if gap > 0 else name_b} by "
            f"{'at least ' if bounded else ''}{abs(gap):.2f}",
        ])

    hover = (
        "%{customdata[0]}<br>"
        f"{name_a} · %{{customdata[1]}} · %{{customdata[2]}}<br>"
        f"{name_b} · %{{customdata[3]}} · %{{customdata[4]}}<br>"
        "<b>%{customdata[5]}</b><extra></extra>"
    )
    fig.add_trace(pgo.Bar(
        # A bar no wider than about 26px on a desktop, however few the events.
        x=xs, y=ys, customdata=custom, width=min(0.72, len(cells) / 42),
        marker=dict(color=fills, line=dict(color=edges, width=widths)),
        hovertemplate=hover, showlegend=False,
    ))
    if level_x:
        fig.add_trace(pgo.Scatter(
            x=level_x, y=[0] * len(level_x), customdata=level_custom, mode="markers",
            marker=dict(symbol="diamond", size=7, color=app._REFERENCE),
            hovertemplate=hover, showlegend=False,
        ))

    app._style_trend_chart(fig, "Gap between the two finishes")
    _year_axis(fig, cells, "Shared events, oldest to newest")
    fig.update_yaxes(
        # A gap cannot pass 1 either way, so the bands beyond it are always clear:
        # each side's label sits in its own.
        range=[-1.2, 1.2], autorange=False, rangemode="normal",
        tickmode="array", tickvals=[-1, -0.5, 0, 0.5, 1],
        ticktext=["1.00", "0.50", "0", "0.50", "1.00"],
        zeroline=True, zerolinecolor=app._rgba(app._REFERENCE, 0.85), zerolinewidth=1,
    )
    for name, colour, y, anchor, arrow in (
        (name_a, colour_a, 1, "top", "▲"), (name_b, colour_b, 0, "bottom", "▼"),
    ):
        fig.add_annotation(
            x=0, y=y, xref="paper", yref="paper", xanchor="left", yanchor=anchor,
            xshift=6, yshift=-2 if anchor == "top" else 2, showarrow=False,
            text=f"{arrow} {name} finished better", font=dict(color=colour, size=12),
        )
    fig.update_layout(height=400, margin=dict(t=12), bargap=0)
    return fig


def gap_bound_caption(series) -> str:
    app = _app()
    if app._has_bounded_point(series.cells):
        return parts.caption(f"{_BOUND_LEGEND_A}.")
    return ""


# --- b: today's lines, the events spaced evenly ---------------------------------------


def spaced_figure(name_a: str, name_b: str, series, readout, legend_title: str) -> pgo.Figure:
    """Today's two lines and band over evenly spaced events, the marks sized to fit."""
    app = _app()
    cells = sorted(series.cells, key=lambda c: c.date)
    colour_a, colour_b = palette.CATEGORICAL[0], palette.CATEGORICAL[1]
    fig = pgo.Figure()

    def flip(norm):
        return None if norm is None else 1 - norm

    xs = list(range(len(cells)))
    drawn = [[c.drawn()[side] for c in cells] for side in (0, 1)]
    fig.add_traces(app._band_traces(
        [(x, flip(na), flip(nb)) for x, (na, _), (nb, _) in zip(xs, drawn[0], drawn[1])],
        colour_a, colour_b,
    ))
    # A ring sized to the room each event has, and no wider than today's.
    size = max(5, min(12, round(420 / len(cells))))
    for side, (group, name, colour) in enumerate(
        (("a", name_a, colour_a), ("b", name_b, colour_b))
    ):
        ring = {**app._observation_marker(colour, over_fill=True), "size": size}
        if size < 8:
            ring["line"] = dict(width=1.5, color=colour)
        symbols = app._bound_symbols(drawn[side])
        pinned = symbols[0] == app._BOUND_SYMBOL
        fig.add_trace(pgo.Scatter(
            x=xs, y=[flip(norm) for norm, _ in drawn[side]],
            customdata=[[f"{c.date:%d %b %Y}", *readout(c, side, d)]
                        for c, d in zip(cells, drawn[side])],
            name=name, legendgroup=group if pinned else None, showlegend=not pinned,
            mode="lines+markers", line=dict(width=1, dash="dash", color=colour),
            # Clipped to the plot, unlike today's: a sliced run would otherwise draw
            # its joins out over the axis title.
            marker={**ring, "symbol": symbols},
            hovertemplate=(f"%{{customdata[0]}} · {name} · %{{customdata[1]}} · "
                           "%{customdata[2]}<extra></extra>"),
        ))
        if pinned:
            fig.add_trace(app._swatch_pin(name, group, colour, ring))

    app._style_trend_chart(fig, f"Finish ({app._SCORE_DIRECTION})")
    _year_axis(fig, cells, "Shared events, oldest to newest")
    fig.update_layout(margin=dict(t=48), height=430)
    # The slim control, so a reader can slice into a run; the page script opens a
    # phone on the latest PHONE_WINDOW events, since eighty do not fit one.
    slim_range(fig)
    # A little past each end, so a win and a last place keep their whole ring.
    fig.update_yaxes(tickformat=numfmt.SCORE_TICKFORMAT, range=[-0.03, 1.03],
                     autorange=False, tick0=0, dtick=0.2, zeroline=False)
    app._midpoint_line(fig)
    fig.update_layout(legend=dict(
        title=legend_title, orientation="h",
        xanchor="center", x=0.5, yanchor="bottom", y=1.02,
    ))
    return fig


# --- the single timeline: points and the 0.5 line -------------------------------------


def slim_range(fig: pgo.Figure) -> None:
    """The range control as a slim neutral strip: no label, no tint."""
    app = _app()
    margin = fig.layout.margin
    plot = (fig.layout.height or 450) - (margin.t or 0) - (margin.b or 0)
    fig.update_xaxes(rangeslider=dict(
        visible=True, thickness=_SLIM_BAND / plot,
        bgcolor=app._rgba(app._AXIS, 0.10), bordercolor=app._GRID, borderwidth=1,
        yaxis=dict(rangemode="fixed", range=[10, 11]),
    ))


def solo_figure(name: str, series) -> pgo.Figure:
    """One archetype's events as points against the 0.5 line: no fill, no join."""
    app = _app()
    cells = sorted(series.cells, key=lambda c: c.date)
    colour = palette.CATEGORICAL[0]
    fig = pgo.Figure()
    fig.add_trace(pgo.Scatter(
        x=[c.date for c in cells],
        y=[None if c.mean_norm_a is None else 1 - c.mean_norm_a for c in cells],
        customdata=[app._bounded_readout(c.mean_norm_a, False, f"{c.decks_a} decks")
                    for c in cells],
        name=name, mode="markers", showlegend=False,
        marker={**app._observation_marker(colour), "size": 7,
                "line": dict(width=1.5, color=colour)},
        cliponaxis=False,
        hovertemplate=(f"%{{x|%d %b %Y}} · {name} · %{{customdata[0]}} · "
                       "%{customdata[1]}<extra></extra>"),
    ))
    app._style_trend_chart(fig, f"Finish ({app._SCORE_DIRECTION})")
    fig.update_xaxes(title="Registration date", type="date", categoryorder=None,
                     autorange=True)
    fig.update_layout(margin=dict(t=12), height=400)
    slim_range(fig)
    fig.update_yaxes(tickformat=numfmt.SCORE_TICKFORMAT, range=[0, 1], autorange=False)
    app._midpoint_line(fig)
    return fig


# --- what is bolted onto the page ----------------------------------------------------


def three(today: str, a: str, b: str) -> str:
    """One heading per variant, the stylesheet showing one."""
    return (f"<div class='p335-today'>{today}</div><div class='p335-a'>{a}</div>"
            f"<div class='p335-b'>{b}</div>")


def gap_caption(today_caption: str | None) -> str | None:
    """Today's caption with the bound's line reworded for the open bar."""
    app = _app()
    if today_caption is None:
        return None
    return today_caption.replace(parts.caption(f"{app._BOUND_LEGEND}."),
                                 parts.caption(f"{_BOUND_LEGEND_A}."))


_PAGE_CSS = """
/* One variant on show. A chart that is not on show keeps its width and gives up its
   height, so the library draws it at the size it will be shown at. */
html[data-proto-variant="today"] .p335-a, html[data-proto-variant="today"] .p335-b,
html[data-proto-variant="a"] .p335-today, html[data-proto-variant="a"] .p335-b,
html[data-proto-variant="b"] .p335-today, html[data-proto-variant="b"] .p335-a {
  display: none !important;
}
html[data-proto-variant="today"] .p335-plot-a, html[data-proto-variant="today"] .p335-plot-b,
html[data-proto-variant="a"] .p335-plot-today, html[data-proto-variant="a"] .p335-plot-b,
html[data-proto-variant="b"] .p335-plot-today, html[data-proto-variant="b"] .p335-plot-a {
  height: 0 !important; min-height: 0 !important; overflow: hidden !important;
  visibility: hidden !important; padding: 0 !important; margin: 0 !important;
  border: 0 !important;
}
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
  const VARIANTS = [["today", "Today"], ["a", "Gap per event"], ["b", "Lines, spaced"]];
  const PHONE_WINDOW = %d;
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
    label.textContent = "Prototype #335 \\u00b7 " + name;
    window.dispatchEvent(new Event("resize"));
  }
  function step(by) { at = (at + by + VARIANTS.length) %% VARIANTS.length; apply(); }
  prev.addEventListener("click", () => step(-1));
  next.addEventListener("click", () => step(1));
  document.addEventListener("keydown", (event) => {
    if (event.target.closest("input, textarea, select, [contenteditable], [role=tab], [role=radio]")) return;
    if (event.key === "ArrowLeft") step(-1);
    if (event.key === "ArrowRight") step(1);
  });
  apply();

  // b at phone width: a pair's chart opens on its latest events, once per draw.
  setInterval(() => {
    if (window.innerWidth > 640 || !window.Plotly) return;
    document.querySelectorAll(".p335-plot-b .js-plotly-plot").forEach((plot) => {
      if (!plot.data || plot.__p335 === plot.data) return;
      plot.__p335 = plot.data;
      const lines = plot.data.filter((t) => t.mode === "lines+markers" && t.x.length > 1);
      if (lines.length < 2 || typeof lines[0].x[0] !== "number") return;  // the single timeline
      const n = lines[0].x.length;
      if (n > PHONE_WINDOW) {
        window.Plotly.relayout(plot, {"xaxis.range": [n - PHONE_WINDOW - 0.75, n - 0.25]});
      }
    });
  }, 400);
}
""" % PHONE_WINDOW


def main() -> None:
    from graph7ph import app

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=Path("data/graph"))
    args = parser.parse_args()

    kwargs = app.launch_kwargs()
    kwargs["css"] += _PAGE_CSS
    kwargs["js"] = _PAGE_JS
    app.build_app(args.db).launch(**kwargs)


if __name__ == "__main__":
    main()
