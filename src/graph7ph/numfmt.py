"""The one numeric convention (v1 visual direction §4), as pure functions.

Every share, count-plus-sample-size, and inverted-finish score the app prints is
written one way, produced here so the strings cannot drift chart to chart. Pure
string functions: no Plotly, no HTML, so the convention is tested on its output
and the renderers only interpolate what these return.
"""


def share(fraction: float) -> str:
    """A share as a trimmed two-decimal percent: ``0.0673`` -> ``"6.73%"``.

    The d3 ``.2~%`` the chart axes carry, in Python: two decimals so a sub-1%
    share reads its real value (``0.12%``) rather than a rounded ``0%`` or a ``<1%``
    that erases the ratio, with insignificant trailing zeros trimmed (``6.7%``,
    ``10%``).
    """
    pct = f"{fraction * 100:.2f}".rstrip("0").rstrip(".")
    return f"{pct}%"


def count_of(count: int, total: int, unit: str = "") -> str:
    """A count against its sample size: ``count_of(134, 2000, "decks")`` ->
    ``"134 / 2,000 decks"``.

    The one sample-size form, retiring the ``n=12`` / ``12/2000 decks`` split:
    both numbers thousands-comma'd, a spaced slash, the unit appended when the
    denominator names one (``"decks"``) and omitted when it does not.
    """
    ratio = f"{count:,} / {total:,}"
    return f"{ratio} {unit}" if unit else ratio


def score(value: float) -> str:
    """An inverted-finish score to two decimals, with the sense once:
    ``0.62`` -> ``"0.62 (1 = 1st)"``.

    The finish flipped so higher is better (1 a win); the parenthetical states
    which end is good once, rather than spelling out both ends on every readout.
    """
    return f"{value:.2f} (1 = 1st)"


# The chart axes generate their own ticks client-side, so they carry the same
# conventions as the functions above in d3's format language, kept here beside them
# so an axis and a readout can never state the same quantity two ways.
# A share axis: two decimals with trailing zeros trimmed, the tick form of share().
SHARE_TICKFORMAT = ".2~%"
# A 0-1 score axis: two fixed decimals for a tabular tick column (unlike the
# trimmed readout); score() carries the sense on the hover.
SCORE_TICKFORMAT = ".2f"
