"""Temporal trends: a tabular ``Series`` result and its own seam (ADR 0013).

A trend is a value per year, which is neither a node nor an edge, so it does not
flow through the ``Subgraph``-returning query spine or ``run_query``. It gets its
own return type, a tabular ``Series``, and its own seam, ``run_series``. Two
result kinds, two seams, neither overloaded.

``meta_share_over_time`` is the tracer: archetype share of the meta per ``Year``,
the first real consumer of the ``IN_YEAR`` edge. Later trend tools (card adoption,
pilot performance) reuse this plumbing; head-to-head, the non-year one, is built
last (ADR 0013).
"""

import calendar
import math
import random
from dataclasses import dataclass
from datetime import datetime

import ladybug

from graph7ph.db import rows

# There is deliberately no meta-share cell floor here. A share is a count over a
# known denominator, not an estimate, so it is a direct observation and ADR 0013's
# rule exempts it; the floor this module used to hold was removed for that reason
# (see the amendment in ADR 0013 before adding one back).

# The per-``(pilot, year)`` event floor: the fewest distinct events a pilot needs in
# a year for that year's mean ``placementNorm`` to be a mean worth plotting rather
# than a single point (ADR 0013, and ADR 0005's refuse-rather-than-report-noise).
# The unit is the **event**, not the deck: an event is one independent tournament
# finish, so a list a pilot reused across events is separate evidence, but two decks
# at one event would not be. In the current graph the two coincide (ADR 0004 already
# folds a pilot to one deck per event, so events equal decks in every one of the 1833
# cells), but the event is the honest unit and guards data where they diverge.
#
# Two, not more: of 1833 ``(pilot, year)`` cells, 947 hold a single event, where a
# "mean" is really that one finish, so the floor gaps those; the rest (two or more
# events) are kept, and each point is labelled on the chart with the event count it
# averages, so a thin two-event mean carries its own sample size rather than being
# silently trusted or silently dropped. Kept as its own constant, distinct from
# the gem floor: that governs a mean over the decks running a card, this a pilot's
# own mean over its own finishes.
MIN_PILOT_YEAR_EVENTS = 2

# A pilot needs this many qualifying years or the tool returns "not enough history"
# rather than a lone point on an empty line (ADR 0013). Two, because a line through
# one point is not a trajectory. Named, not a bare literal, because the rule lives in
# two places (the tool and its dropdown catalogue) and must not drift between them.
MIN_QUALIFYING_YEARS = 2

# A pilot pair needs this many shared events or it is a dot, not a timeline, so the
# head-to-head tool returns nothing rather than a lone point (ADR 0013). Each point
# is one real registration, so there is no within-point floor; the floor is on the
# pair, not the event. Two, the same reason as MIN_QUALIFYING_YEARS: one point is not
# a trajectory.
MIN_SHARED_EVENTS = 2

# The fewest scored events an archetype timeline needs: for one archetype the events
# it was ranked at, for a pair the events both were ranked at. Two, the same reason as
# MIN_QUALIFYING_YEARS and MIN_SHARED_EVENTS: one point is not a trajectory, and with
# two archetypes a lone shared event is a comparison, not a rivalry over time.
#
# The floor counts **comparable** events rather than attended ones, which is where it
# parts from MIN_SHARED_EVENTS beside it. A pilot brings one deck to an event, so a
# shared event is a comparison by construction; an archetype's point is a mean over
# whichever of its decks the source scored, so a shared event can hold nothing to
# compare on one side, and counting those would clear the floor on a chart with a
# single drawable point and a gap. Refusal is a common path here, not an edge case:
# over the 105 archetypes with 5-plus ranked events, the median pair of 5460 shares
# just 4 events, 9% share none and 21% share one or fewer.
#
# The same constant gates the selector catalogue (:func:`archetypes_with_history`), so
# a single archetype offered on the surface always draws; a pair cannot be precomputed
# (it is pairwise, and 121 archetypes make 7260 pairs), so a pair still lands on the
# refusal and the surface states the count it found.
MIN_ARCHETYPE_EVENTS = 2

# The fewest plottable archetypes a year needs before its landscape is a landscape.
# Three, because the chart's whole claim is relative: a dot reads as popular or niche,
# and winning or losing, only against a field of other dots, and a pair is a comparison
# rather than a field. It counts the archetypes with a finish to plot, not the ones that
# turned up, since an archetype the year never scored has no y to place it at. No corpus
# year comes near it (the thinnest, 2023, holds 56 archetypes over 8 events), so this
# guards a future thin year and a year picked from outside the data, not today's.
#
# This is a floor on the **field**, not on the value, and the landscape deliberately
# carries no per-archetype event floor beside it, though its y is a mean of finishes and
# ADR 0013's rule would otherwise pin one there (as MIN_PILOT_YEAR_EVENTS does for the
# pilot's own mean). Measured and rejected in issue #145, and the two thick years and the
# two thin ones are rejected for different reasons. In the thick years popularity already
# carries reliability at this cut: the minimum inside the drawn top 25 is 11 distinct
# events for both 2025 and 2026, and across their top 50 exactly one archetype (2025) is
# under 5 events. In the thin years a floor cannot rescue the year, only empty it: the
# drawn top 25's minimum is 5 events in 2024 and 1 in 2023, but 2023 holds 8 events in
# total, so a floor of 5 would leave about 9 dots, which is not a landscape either.
# What guards a thin dot is therefore the evidence carried beside it rather than a cut:
# every cell ships its distinct event count, the marker size draws it (a one-event dot
# is the smallest ring on the chart), the hover states it, and the caption states the
# whole year's events and decks, so 2023 announces its 8 events on the surface.
MIN_LANDSCAPE_ARCHETYPES = 3


# The field size above which an event is a **major**, and with
# :data:`MIN_FIELD_COVERAGE` one of the two things the player leaderboard requires before
# it scores an event at all (issue #135). Over 64 is 21 of the 107 events on the current
# artifact, 19.6%, near enough the top fifth by size (two of the 21 published a bracket
# rather than a field, so 19 are scored); the field-size deciles run 3, 15, 22, 24,
# 30, 35, 39, 45, 70, 102, 306, so the cut sits inside the top decile's shoulder rather
# than on a cliff. Scoring majors only costs reliability and was chosen anyway: the
# split-half reliability of a career score is rho 0.38 over majors against 0.62 over all
# events. An all-events score is not comparable across pilots, which is worse than
# noisy: contenders' records run from 18% majors to 100%, median 62%, so ranking a
# mostly-locals record against a majors-only one treats two different measurements as
# one. Locals are not the softer field, either, so this is not a strength adjustment
# wearing a size threshold: within-pilot, a contender's mean at locals sits 0.041 *below*
# their mean at majors at 3.1 standard errors, because the normalised scale is simply
# finer above a strong player in a large field.
MAJOR_FIELD_SIZE = 64

# The fraction of a field whose finishes have to be on record before that event is
# scored at all. Some events publish a top-8 bracket rather than standings, and at those
# the only finishes that exist are good ones: SSWam holds 7 results against a field of
# 88, ANZSS10 holds 8 against 81, and neither records a finish deeper than 5th. Everyone
# who attended and busted is simply absent, so attending becomes a free roll (cut and the
# record gains a career-best finish, bust and it never happened). A cut-only finish
# scores 0.967 on average against 0.506 at a full-field major, which is what selecting on
# the answer is worth. The race is a *ranking*, so a record built from one of those is
# not comparable to a record built from standings, and it is dropped rather than
# discounted: there is no number of good finishes that tells you how many bad ones were
# withheld.
#
# Coverage is bimodal on this artifact and the threshold sits in the empty band. The 26
# published brackets top out at 42.1% and real records start at 78.9%, with one event
# between them (GGWAD, 16 finishes of 28) which is a thinned standings rather than a
# bracket and is kept. So the live gap is 42.1% to 57.1%, about 8 points of slack either
# side of the cut, and every event keeps its side anywhere inside it. Past 0.58 GGWAD
# flips to dropped, which is the nearest thing to a real edge and is 8 points away. It
# costs the race 15 finishes of 2,387 and two of 21 majors.
MIN_FIELD_COVERAGE = 0.5

# The two gates on who is ranked at all, both career-relative and both re-derived on
# every rebuild. A contender needs this many majors in their whole career, so a score
# rests on a record rather than a weekend...
MIN_CAREER_MAJORS = 5

# ...and this many inside the recency window, so the race holds current contenders and
# not a hall of fame. Together they leave 137 of the 852 pilots with any scored finish.
# The recency half is also what guarantees every plotted line reaches the right edge of
# the chart: a contender is by definition still playing.
MIN_RECENT_MAJORS = 2

# How far back "still playing" reaches, in months from the newest event in the graph.
RACE_RECENCY_MONTHS = 12

# The race's x axis: this many sample dates, stepped this many months apart, the newest
# at the newest event in the graph so the chart stays current across a rebuild.
#
# A sample date is an "as of", not a bucket: a pilot's score there counts every major
# they had played up to that date, so the series is their record accumulating rather
# than their form moving. That is the whole point, and it replaces an 18-month rolling
# window (ADR 0017). The window version was measured and its movement was noise:
# reshuffling each contender's own finishes across their own event dates, which destroys
# every trace of when they played well, left the mean within-pilot swing across the
# windows at 0.0885 (90% range 0.0823 to 0.0947) against an observed 0.0915. Three
# percent of the visible movement was real. A rolling window over a record this thin
# (median 4 majors a point, minimum 2) cannot see form, so the chart stopped claiming to.
RACE_STEP_MONTHS = 6
RACE_POINTS = 5

# The fewest majors a pilot needs behind a sample date before its score is drawn
# (ADR 0013's floor on an aggregate). Two, as everywhere else in this module: one finish
# is not a mean. It bites only at the left of the chart now, since a running score can
# lose a point to a thin start but never to a quiet spell.
MIN_SCORED_MAJORS = 2

# The resampling that puts an interval on a career rank, and the seed that makes it the
# same interval on every build (the app and the oracle both have to be able to rebuild a
# figure and get it back). One thousand resamples runs in under half a second on the
# current field and is far more than the interval's own precision needs: at 200 the top
# eight's bounds already sit within a rank or two of where 1000 puts them.
#
# The interval is the reason the tool reports rank uncertainty at all. Scores at the top
# of this board are separated by thousandths against a record of median 8 majors, and a
# bare rank column presents an ordering the evidence does not support: on the current
# field only 4.9 of the top 8 survive a resample of their own results, and 16 contenders
# hold a one-in-ten claim on a top-eight place. The interval is what makes a row say so.
RACE_RESAMPLES = 1000
RACE_RESAMPLE_SEED = 20260727
RACE_INTERVAL = 0.90

# The fewest contenders a race needs. Two, because a race is relative: one line alone
# says nothing about who was best, only what one pilot scored, which is what the
# single-pilot performance chart is already for.
MIN_RACE_CONTENDERS = 2


# The multiplier on a mean's standard error that makes a 90% interval: the two-sided
# normal cut, the same 90% the race's rank interval reports (:data:`RACE_INTERVAL`), so
# every claim in the app is settled at one confidence and a reader learns the level once.
#
# It is a normal cut and not a t one, which is the deliberate simplification: the
# standard error divides a **field-pooled** spread rather than the point's own two or
# three finishes, so the degrees of freedom behind it are the field's (thousands) and
# not the point's, where a t cut is 1.645 to three decimals. Pooling is the whole point.
# The obvious alternative, bootstrapping each point's own finishes, was simulated
# against this artifact's noise and rejected: a nominal 90% interval covers 50.6% at two
# finishes and 62.1% at three, because two finishes admit only three distinct resampled
# means and no bootstrap variant escapes that (issue #175). The pooled form covers 90.0
# to 90.9% at every one of those sample sizes on the same simulation, and any method
# proposed to replace it has to clear that bar first.
INTERVAL_Z = 1.645

# The fewest finishes a pilot's career needs before it is one of the records the
# within-pilot spread is pooled over. Five, so the term is estimated on records long
# enough to carry a spread of their own, the same size of record :func:`_shrinkage`
# fits on (:data:`MIN_CAREER_MAJORS`). That is 257 pilots and 3,003 finishes, where
# issue #175 measured 272 and 3,209 over a corpus that still counted the
# bracket-only events. It is a fitting floor, not a drawing one:
# every drawn point gets an interval from it, including the two-event years no record
# here is thin enough to reach.
MIN_SPREAD_FINISHES = 5


def _pooled_sd(records: list[list[float]]) -> float | None:
    """How far one observation bounces from its own record's level, pooled over a field.

    The one input every claim interval in this module takes: the within-record spread,
    estimated once over the whole field rather than off the handful of observations
    behind a single drawn point. It is :func:`_shrinkage`'s ``within`` term standing on
    its own, as a standard deviation, because two surfaces need that quantity without
    needing a shrunk score (issue #175).

    A record of one observation contributes to neither the sum of squares nor the
    degrees of freedom: it has no spread of its own to pool. A field where nothing was
    observed twice therefore comes back ``None``, which says the field cannot state how
    much one observation bounces, and the caller draws no interval rather than a zero
    one.

    Each record is summed in sorted order, because float addition is not associative and
    Ladybug hands the same rows back in different orders between calls: an unsorted
    record makes the same artifact report bounds that differ in the last decimals, which
    is exactly the "a moved number means moved evidence" promise the interval exists to
    keep. Callers pass the records themselves in a settled order for the same reason.
    """
    squares, dof = 0.0, 0
    for record in records:
        if len(record) < 2:
            continue
        record = sorted(record)
        mean = sum(record) / len(record)
        squares += sum((x - mean) ** 2 for x in record)
        dof += len(record) - 1
    return math.sqrt(squares / dof) if dof else None


def _interval(mean: float, n: int, sd: float | None) -> tuple[float, float] | None:
    """The 90% interval on a mean of ``n`` observations, given the field's spread.

    ``mean`` plus and minus :data:`INTERVAL_Z` standard errors, the standard error being
    the pooled ``sd`` over the root of the point's own sample. The bounds are clamped to
    the 0-to-1 scale a ``placementNorm`` lives on, since no mean finish can be better
    than a win or worse than last; clamping only ever moves a bound toward the truth, so
    the coverage the pooled form was chosen for is not spent on it.

    ``None`` where there is no spread to divide (a field nothing was observed twice in)
    or no sample to divide it over (a year the source scored nothing of). Both are "this
    cannot be said", which the surfaces draw as no interval rather than as a zero-width
    one that would read as a settled point.
    """
    if sd is None or n < 1:
        return None
    half = INTERVAL_Z * sd / math.sqrt(n)
    return max(0.0, mean - half), min(1.0, mean + half)


class NotEnoughHistory(ValueError):
    """Raised when a trend is refused for want of evidence, and says how much it found.

    Raised for the reason ``query.SliceTooSmall`` was until #184 retired it: an empty
    result is not an answer, it is four answers wearing one coat. ``Series(cells=[])`` used
    to mean "refused as too thin", "never played", "never met" and "met once" alike,
    so the agent seam could not tell a refusal from a zero (issue #101). Raising
    names the refusal, and the message carries the count that caused it, so a pair
    who met once is distinguishable from a pair who never met.

    It does not distinguish a pilot key the graph has never heard of, which still
    arrives here as a pilot with no history. That is a caller error rather than an
    answer about the format, and no surface can reach it: both trend dropdowns are
    built from the graph's own pilots.
    """

    def __init__(self, message: str, found: int):
        super().__init__(message)
        # The evidence actually found: qualifying years, or shared events. Carried as
        # a number as well as words so a caller can say "they met once" rather than
        # having to read it back out of the sentence.
        self.found = found


@dataclass(frozen=True)
class SeriesCell:
    """One archetype's share of the meta in one year.

    ``n`` is the count of decks of this archetype (by its primary archetype) in
    this year, and ``share`` is ``n / year_total``, always stated and never
    withheld: a share is a direct observation, so a low count is the signal of an
    archetype entering or leaving the format, not the noise a floor exists to gap
    (the same reading :class:`AdoptionCell` gives a card, ADR 0013). ``year_total``
    is every deck that year, the base the share is of, returned so a coarse year is
    visible (a thin year is honest, not dropped) and so a small share is read
    against the sample it came from. A cell of ``n == 0`` is a real zero, the
    archetype absent that year. ``tag`` is the archetype's stable key, ``archetype``
    its display name (two tags can share a name, so the tag is what identifies it).
    """

    tag: str
    archetype: str
    year: int
    n: int
    share: float
    year_total: int


@dataclass(frozen=True)
class AdoptionCell:
    """One card's adoption in one year: a direct observation, so no floor.

    ``count`` is the decks running the card that year, ``year_total`` every deck
    that year, and ``share`` is ``count / year_total`` always, never withheld: a
    low count is the signal (a card entering the format), not the noise a floor
    exists to gap (ADR 0013). The base rides along so 2 of 192 in a thin year is
    not misread against 2 of 2095 in a fat one. A count of zero is a real year the
    card sat out, kept so the timeline shows it entering rather than a gap.
    """

    year: int
    count: int
    share: float
    year_total: int


@dataclass(frozen=True)
class PerformanceCell:
    """One pilot's finish in one year they played: a mean, or a stated refusal.

    ``mean_norm`` is the mean ``placementNorm`` of the pilot's ranked decks that
    year (0 is a win, 1 is last), and ``events`` the count of distinct events those
    decks were played at, the number of independent finishes the mean is taken over,
    always alongside so the reader has the sample size in hand. ``events`` counts
    **scored** events, so a year the source placed none of is a real zero here, and a
    cell of zero events is a year the pilot played and nobody recorded. A year below
    :data:`MIN_PILOT_YEAR_EVENTS` still gets a cell, with ``mean_norm`` ``None`` and
    its real ``events``: the mean is refused (a thin mean can land anywhere by luck)
    but the year the pilot played is a fact, and dropping the row hid it. The cell
    is the refusal, stated with the sample size that caused it, so "too thin to say"
    and "did not play" stay different answers (ADR 0013's amendment, issue #101).

    ``mean_low`` and ``mean_high`` bound ``mean_norm``: the 90% interval on it, the
    field's within-pilot spread over the root of ``events`` (:func:`_interval`). They
    are on the same raw scale as the mean, so ``mean_low`` is the *better* end and only
    the chart's flip turns that around. They are what makes the cell readable: the
    movement between one year's mean and the next is statistically indistinguishable
    from shuffling a pilot's own finishes across their own years, so the value and its
    width are the whole honest content of the point and a dip is not a slump (issue
    #175). Both are ``None`` wherever ``mean_norm`` is, and also where the field was too
    thin to fit a spread at all.
    """

    year: int
    mean_norm: float | None
    events: int
    mean_low: float | None
    mean_high: float | None


@dataclass(frozen=True)
class LandscapeCell:
    """One archetype's place in one year's metagame: how played it was, how it did.

    The two axes of the landscape in one row. ``n`` is the decks of this archetype
    (by its primary archetype, so each deck is counted once) in this year and
    ``share`` is ``n / year_total``, the same direct observation over the same base
    :class:`SeriesCell` states, never withheld. ``mean_norm`` is the mean
    ``placementNorm`` of the archetype's **scored** decks that year, left raw (0 is a
    win) as :class:`PerformanceCell` leaves it, so only the chart flips it; a deck the
    source never scored neither shifts the mean nor pads the count, and neither does one
    played at an event that published a bracket rather than a field
    (:func:`_cut_only_events`, ADR 0022). ``scored`` is how many decks did count, and
    ``events`` the distinct events they were played at, the independent trials the mean
    rests on, which is why it can be smaller than the events the archetype's decks
    span. An archetype whose year holds no such deck at all has ``mean_norm``
    ``None``, ``scored`` and ``events`` zero: it was played, and nothing about its
    finish is known.

    **The two axes count different decks, and always have.** ``n`` and ``share`` are
    every deck of the archetype; ``scored``, ``events`` and the mean are the decks whose
    finish is worth reading. That is why ``scored`` is carried rather than left to be
    inferred from ``n``: the hover states both, so a reader is never shown a mean over
    29 decks beside a count of 34 with nothing saying which is which.

    ``mean_low`` and ``mean_high`` bound ``mean_norm``, the 90% interval on it over the
    year's own within-archetype spread (:func:`_interval`), raw as the mean is, so
    ``mean_low`` is the better end. They are what the caption's "above the 0.5 line"
    reading has to clear before it is a reading at all: at these sample sizes most dots
    do not settle which side of the middle they belong on (issue #175). The interval
    divides by ``events``, not by the decks, for the reason ``events`` is carried at all:
    several decks of one archetype at one event met one field, so they are one trial.
    Both are ``None`` where ``mean_norm`` is, and where the year held nothing to fit a
    spread over.

    ``year_total`` and ``year_events`` are the year's own decks and events, the
    season the whole landscape rests on, carried on every cell the way
    :class:`SeriesCell` carries its year total, so a caption can state the sample
    without a second query. ``tag`` is the archetype's stable key and ``archetype``
    its display name (two tags can share a name, so the tag is what identifies it).
    """

    tag: str
    archetype: str
    year: int
    n: int
    share: float
    year_total: int
    year_events: int
    mean_norm: float | None
    scored: int
    events: int
    mean_low: float | None
    mean_high: float | None


@dataclass(frozen=True)
class HeadToHeadPoint:
    """One shared event in two pilots' rivalry: a direct observation, so no floor.

    Each point is one real registration, not an aggregate, so it carries no
    within-point floor (ADR 0013); the floor lives on the pair (at least
    :data:`MIN_SHARED_EVENTS` shared events) rather than the event. ``date`` is the
    event's registration date, the earliest ``createdAt`` across its whole field,
    the same proxy ADR 0006 dates the event by but at day rather than year
    granularity, so both pilots' points share one x per event and the two lines
    align. ``field_size`` is the size the norm beside it was ranked against, read off
    ``Event.fieldSize``: the build divides by that number and stores it, so the label
    and the norm cannot disagree (:func:`build.corrected_field`). That is the source's
    own ``eventSize`` at 98 of 107 events and the corrected field at the 9 the build
    re-ranks (issue #140). Where it comes from the source it is the source's published
    entrant count wherever the source publishes one (36 of 107 events carry a
    ``players`` field, and ``eventSize`` equals it in 36 of 36) and the last recorded
    placement on the other 70 of 70, and at the 4 ``eventType='Teams'`` events it
    counts teams rather than people (TMCTeams25 is 39 against 117 decks). It is not
    the decks-at-event count, which it exceeds at 56 of 107 events: a top-cut event
    records only its top finishers and a teams event folds many decks onto few
    places, so the decks-at-event count is neither, and a raw finish is only readable
    against the field the norm actually used.

    Reading the node replaces recovering the field by inverting a norm, which is what
    this did until issue #162. The inversion agreed with the node wherever a norm was
    invertible, but an event holding no norm above 0 yielded nothing and fell back to
    the decks-at-event count: 7 at Area52IQ, 8 at Pats Birthday Brawl, 5 at
    DeckaDiceIQ, against 24 at all three. That was harmless only while those events
    had no norms to draw. Minting gave them some, and Area52IQ's is a win, norm 0.0
    and uninvertible, so the fallback would have labelled it "1st of 7" (ADR 0016).
    The annotation is ``int`` rather than ``int | None`` because all 107 events carry
    a field and Rule C fills a null ``eventSize`` for a Tournament; the source has
    never shipped a non-Tournament event without one, which is the only shape
    :func:`build.corrected_field` would pass a null through.

    ``placement_a``/``norm_a`` are pilot ``a``'s raw finish and ``placementNorm``,
    ``_b`` pilot ``b``'s. A norm no rule could recover is ``None``, and so is a
    placement: 24 decks carry neither, all at 4 events, and their
    ``placementImputed`` says ``none`` rather than leaving a null that reads like a
    number the source chose not to give. The y-axis is ``norm`` (comparable across
    field sizes); the raw placement and field size ride along for the point's label.

    The three ``_imputed`` fields are the rules behind the three numbers, read off
    ``Event.fieldImputed``, ``Deck.normImputed`` and ``Deck.placementImputed``: null
    where the source's own value stands, a rule name where a pass here produced it
    (ADR 0016). They ride the point because the label is what a reader sees, and a
    decided number and a counted one render identically without them (issue #166):
    Pats Birthday Brawl's field of 24 is Rule B's domain rule while SSWam's 88 is an
    entrant count, and a point carrying only the numbers asserts both as the same
    kind of fact. Null is a claim of its own here, not an absence, so all three are
    required rather than defaulted: a caller that leaves one out would be asserting
    the source's authorship of a number it never looked at.

    A value no rule could recover reads ``none`` rather than null, and a point in
    that state draws nothing: 24 decks carry neither placement nor norm, and no deck
    in the record carries a norm without a placement. So a drawn label's placement
    and norm rules are either null or a real rule, never ``none``.
    """

    event: str
    date: datetime
    field_size: int
    field_imputed: str | None
    placement_a: int | None
    norm_a: float | None
    placement_b: int | None
    norm_b: float | None
    placement_imputed_a: str | None
    norm_imputed_a: str | None
    placement_imputed_b: str | None
    norm_imputed_b: str | None


@dataclass(frozen=True)
class ArchetypeTimelinePoint:
    """One event in an archetype's history, or in two archetypes' rivalry.

    The archetype-scale sibling of :class:`HeadToHeadPoint`, and the one place the
    borrowed form breaks: a pilot brings one deck to an event, but an archetype
    brings several, so ``mean_norm_a`` is the **mean** ``placementNorm`` of side
    ``a``'s ranked decks at this event rather than one real result. ``decks_a`` is
    how many decks that mean rests on, carried on every point because it is usually
    tiny: measured over the whole graph, 87% of ``(archetype, event)`` points rest on
    one to three ranked decks and the median is one. ``_b`` is the second archetype's
    pair of the same, ``(None, 0)`` throughout when only one archetype was asked for.

    A deck the source never scored is left out of the mean, so an event an archetype
    attended with nothing ranked comes back ``mean_norm`` ``None`` on ``decks`` zero:
    the attendance is a fact, the finish is not known, and the line breaks over it
    rather than fabricating a point (ADR 0013).

    ``date`` is the event's registration date, the earliest ``createdAt`` across its
    whole field, the same per-event proxy :class:`HeadToHeadPoint` carries. It is
    derived per event and not per deck (which is what the pilot timeline effectively
    does, one deck being one point): an event is not a single date, 21 of 107 spread
    over more than a day and one over 12, and both sides of a shared event have to
    sit at the same x or the band between them is drawn against a lie.
    """

    event: str
    date: datetime
    mean_norm_a: float | None
    decks_a: int
    mean_norm_b: float | None
    decks_b: int


@dataclass(frozen=True)
class RaceCell:
    """One contender at one sample date of the player leaderboard: what was known by then.

    The race's row is a ``(pilot, date)`` pair, so a contender holds one cell per sample
    point and the whole field's trajectory is one table. ``pilot`` is the pilot key; the
    display name is not carried, because the app already holds the disambiguated label
    for every pilot (as the other two pilot trends do).

    ``rank``, ``score`` and ``majors`` are the pilot's **career** standing: their place
    in the field, the shrunk mean over every major they were placed at, and the number
    of those majors. All three repeat on each of the pilot's rows, so the leaderboard
    and the lines come off one table rather than two queries. The rank is carried
    rather than left to the reader to count off, so a tie shares its place instead of
    being split by the order a table happened to iterate in. ``majors`` is the score's
    own sample, so it can never exceed the events the score rests on. ``major_events``
    is how many majors the graph holds in all, the pool every score is drawn from,
    carried on every cell the way :class:`LandscapeCell` carries its year's totals so a
    caption can state it without a second query.

    ``rank_low`` and ``rank_high`` bound the career rank: the
    :data:`RACE_INTERVAL` interval over :data:`RACE_RESAMPLES` resamples of every
    contender's own finishes, rebuilt end to end each time so the shrinkage moves with
    the resampled field rather than being held at the point estimate's value. They are
    the honest width of ``rank``, and on the current field they are wide: rank 6 spans
    1 to 40 and the median contender's spans 65 of the 137 places. Carried on the cell
    rather than derived by a caller, because the interval
    depends on the whole field's records and no consumer holds those.

    ``as_of`` is the date this row's score was taken at, and ``as_of_score`` is the same
    shrunk statistic over every major the pilot had played **up to and including** it,
    not over a window ending there (ADR 0017). ``as_of_majors`` is how many majors that
    was, so it rises across a pilot's row and its last value is ``majors``. The score is
    ``None`` where fewer than :data:`MIN_SCORED_MAJORS` majors stand behind it: the floor
    is on the value, not on the row, so a thin start still states the sample size that
    refused it (ADR 0013's amendment, the same shape :class:`PerformanceCell` keeps).

    ``as_of_rank`` is the pilot's standing among the contenders scored by that date,
    ``None`` where they were not, and ``as_of_contenders`` is how many that was, carried
    beside the rank the way ``majors`` is carried beside the score: the field a date
    ranks over is whoever had a record by then, which at the left of the chart is most
    of the way to the whole field but not all of it, so a rank read against the final
    field would overstate it. It ranks against that field rather than positioning the
    line, because rank is not comparable across the span while the score is: the scored
    pool grows over the chart, so a held rank would draw as a flat line across a pilot
    whose evidence went from nothing to conclusive.
    """

    pilot: str
    rank: int
    score: float
    majors: int
    major_events: int
    rank_low: int
    rank_high: int
    as_of: datetime
    as_of_score: float | None
    as_of_majors: int
    as_of_rank: int | None
    as_of_contenders: int


@dataclass
class Series:
    """A tabular trend result: the full matrix of cells, no truncation.

    Distinct from ``Subgraph`` (nodes and edges the renderer draws). The trend tab
    reads these as a line chart and the future v2 agent reads them as numbers, off
    the same result (ADR 0013). Each trend fills the matrix with its own cell type:
    :class:`SeriesCell` for the meta-share matrix, :class:`AdoptionCell` for one
    card's per-year adoption.
    """

    cells: (
        list[SeriesCell] | list[AdoptionCell] | list[PerformanceCell]
        | list[HeadToHeadPoint] | list[LandscapeCell] | list[ArchetypeTimelinePoint]
        | list[RaceCell]
    )


@dataclass(frozen=True)
class MetaShareOverTime:
    """Spec for :func:`meta_share_over_time`; takes no argument (the whole meta)."""


@dataclass(frozen=True)
class CardAdoptionOverTime:
    """Spec for :func:`card_adoption_over_time`; takes a card's ``canon``.

    ``board`` scopes the count: ``None`` counts a deck running the card in either
    board, ``"Main"`` or ``"Side"`` only the decks running it there, mirroring the
    board filter the card-usage query already offers.
    """

    canon: str
    board: str | None = None


@dataclass(frozen=True)
class PilotPerformanceOverTime:
    """Spec for :func:`pilot_performance_over_time`; takes a pilot's ``pilot`` key."""

    pilot: str


@dataclass(frozen=True)
class HeadToHeadTimeline:
    """Spec for :func:`head_to_head_timeline`; takes two pilots' ``pilot`` keys."""

    a: str
    b: str


@dataclass(frozen=True)
class PlayerLeaderboard:
    """Spec for :func:`player_leaderboard`; takes no argument (the whole field)."""


@dataclass(frozen=True)
class ArchetypeLandscape:
    """Spec for :func:`archetype_landscape`; takes the calendar ``year`` to draw."""

    year: int


@dataclass(frozen=True)
class ArchetypeTimeline:
    """Spec for :func:`archetype_timeline`: one archetype's ``tag``, or two.

    ``b`` is optional, which is what lets the surface draw as soon as one archetype
    is picked: a solo timeline is the whole tool, and a second archetype turns it into
    a head-to-head over the events the two shared.
    """

    a: str
    b: str | None = None


SeriesSpec = (
    MetaShareOverTime | CardAdoptionOverTime | PilotPerformanceOverTime
    | HeadToHeadTimeline | ArchetypeLandscape | ArchetypeTimeline | PlayerLeaderboard
)


def _year_totals(conn: ladybug.Connection) -> dict[int, int]:
    """Every year's total deck count, the base each year's shares are read against.

    The shared year denominator ADR 0013 anticipates: the thin internal helper the
    group-by trends may share (meta share and card adoption both divide by it). A
    year exists here only because decks played in it, so a year's total is always
    positive and no share ever divides by zero.
    """
    return {
        year: total
        for year, total in rows(conn.execute(
            """MATCH (d:Deck)-[:PLAYED_AT]->(:Event)-[:IN_YEAR]->(y:Year)
               RETURN y.year, count(DISTINCT d)"""
        ))
    }


def _year_shape(conn: ladybug.Connection, year: int) -> tuple[int, int]:
    """One year's decks and distinct events: the season a landscape rests on.

    The year-scoped sibling of :func:`_year_totals`, which counts decks alone across
    every year. The event count rides along because the landscape's caption states
    both (a partial year is honest only if the reader can see how much of a season it
    holds). Both counts come off one projection: the two ``count(DISTINCT ...)``
    agree with the same counts taken separately over all four corpus years. A year the
    graph has never heard of comes back ``(0, 0)`` rather than no row, since an
    ungrouped aggregate always yields one.
    """
    ((decks, events),) = rows(conn.execute(
        """MATCH (d:Deck)-[:PLAYED_AT]->(e:Event)-[:IN_YEAR]->(:Year {year: $year})
           RETURN count(DISTINCT d), count(DISTINCT e)""",
        {"year": year},
    ))
    return decks, events


def _within_archetype_sd(
    conn: ladybug.Connection, year: int, skip: list[str]
) -> float | None:
    """One year's within-archetype spread: how far one deck finishes from its engine's.

    The landscape's fit for :func:`_interval`, the archetype-scale sibling of
    :func:`_within_pilot_sd`. It is fitted per year because the query is already
    year-scoped and nothing is bought by widening it: measured across the corpus the
    figure is 0.297 / 0.290 / 0.291 / 0.292 for 2023 to 2026, which the deck-norm scale
    pins near the 0.289 a uniform 0-to-1 spread would give, so a whole-corpus fit would
    move no bound a reader could see.

    It is fitted over the same finishes the chart draws, so a bracket-only event is no
    part of it either (:func:`_cut_only_events`, ADR 0022): the spread wanted is of the
    quantity being plotted, which is :func:`_within_pilot_sd`'s reason too. The ``skip``
    is passed in rather than read here, since the caller needs the same list for the
    mean and it is a grouped scan of every ranked deck.

    It pools **deck** norms while :func:`_interval` divides by the archetype's distinct
    events, which is deliberately the conservative pairing: the decks are what there is
    a spread of, the events are the independent trials, and where they differ the wider
    interval is the honest one. A year holding no archetype with two scored decks comes
    back ``None`` and its dots carry no interval, which is the right answer for a year
    that cannot say how much one deck bounces.
    """
    by_tag: dict[str, list[float]] = {}
    for tag, norm in rows(conn.execute(
        """MATCH (d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype),
                 (d)-[:PLAYED_AT]->(e:Event)-[:IN_YEAR]->(:Year {year: $year})
           WHERE d.placementNorm IS NOT NULL AND NOT e.event IN $skip
           RETURN a.tag, d.placementNorm""",
        {"year": year, "skip": skip},
    )):
        by_tag.setdefault(tag, []).append(norm)
    return _pooled_sd([norms for _, norms in sorted(by_tag.items())])


def archetype_landscape(conn: ladybug.Connection, year: int) -> Series:
    """One year's metagame as share against finish, one cell per archetype played.

    The two questions the Meta tab keeps apart: how much of the year an archetype was,
    and how it did. Decks are grouped by their primary archetype, so each deck is
    counted once (a deck normally belongs to one engine outright, and counting every
    tag would credit a deck to an archetype on 5% weight and push the year's shares
    past 100%); the share's base is every deck that year, the same base
    :func:`meta_share_over_time` divides by. The finish is the mean ``placementNorm``
    over the archetype's scored decks, with the decks and the distinct events those
    decks sat at beside it, so a mean is never read without the independent trials
    behind it.

    **The two axes read different events, on purpose (ADR 0022).** The share counts
    every deck the source shipped, a bracket's included: they were genuinely played, and
    dropping them would trade real sample for a correction no reader could see (0.14pp
    at worst, over 193 decks of 4,590). The finish reads only the events that published
    a field (:func:`_cut_only_events`), because a bracket records the decks that cut and
    nothing else, so a mean over one is a mean over decks selected on their own answer.
    That is the split :class:`LandscapeCell` already carried between every deck and the
    scored ones, widened by one condition rather than a new contract.

    Only the archetypes the year actually held get a cell. The meta-share matrix is
    rectangular so a line can drop to a real zero across a year; a scatter has no line
    to break, and an archetype with no decks has no share to plot and no finish to
    plot it against, so it is absent rather than a dot at the origin.
    """
    total, year_events = _year_shape(conn, year)
    names = dict(rows(conn.execute("MATCH (a:Archetype) RETURN a.tag, a.name")))
    counts = dict(rows(conn.execute(
        """MATCH (d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype),
                 (d)-[:PLAYED_AT]->(:Event)-[:IN_YEAR]->(:Year {year: $year})
           RETURN a.tag, count(DISTINCT d)""",
        {"year": year},
    )))
    # Read once and threaded through, as `pilot_performance_over_time` reads it: the
    # mean and the spread it is widthed by both need it.
    skip = _cut_only_events(conn)
    # The mean and its sample over the scored decks only: a null placementNorm is a deck
    # with no finish on record at all, from the source or from this project (ADR 0016),
    # so it neither shifts the mean nor pads the deck or
    # event count. Nor does a finish at an event that published only its bracket, where
    # a recorded finish is a good one by construction. **Keep `count(DISTINCT e)` last
    # in this projection.** A non-DISTINCT aggregate placed after a DISTINCT count in a
    # grouped projection comes back silently wrong on Ladybug (`avg()` NaN, `count()`
    # zero, no error raised); swapping these two returns NaN for the mean on all 112 of
    # 2025's archetypes. It is the order, not the combination, and not the number of
    # groups: see `docs/research/7phstats-insight-gap.md`, and the same ordered shape in
    # `pilot_performance_over_time`. Issue #145's own note prescribes splitting this
    # into two queries instead, which is the superseded diagnosis, not a second rule.
    finishes = {
        tag: (mean, decks, events)
        for tag, mean, decks, events in rows(conn.execute(
            """MATCH (d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype),
                     (d)-[:PLAYED_AT]->(e:Event)-[:IN_YEAR]->(:Year {year: $year})
               WHERE d.placementNorm IS NOT NULL AND NOT e.event IN $skip
               RETURN a.tag, avg(d.placementNorm), count(d), count(DISTINCT e)""",
            {"year": year, "skip": skip},
        ))
    }
    sd = _within_archetype_sd(conn, year, skip)
    cells = []
    for tag, n in sorted(counts.items()):
        mean, scored, events = finishes.get(tag, (None, 0, 0))
        bounds = _interval(mean, events, sd) if mean is not None else None
        low, high = bounds or (None, None)
        cells.append(LandscapeCell(
            tag=tag,
            archetype=names[tag],
            year=year,
            n=n,
            share=n / total,
            year_total=total,
            year_events=year_events,
            mean_norm=mean,
            scored=scored,
            events=events,
            mean_low=low,
            mean_high=high,
        ))
    plottable = [cell for cell in cells if cell.mean_norm is not None]
    if len(plottable) < MIN_LANDSCAPE_ARCHETYPES:
        raise NotEnoughHistory(
            f"{year} has {len(plottable)} archetype(s) with a finish to plot; "
            f"a landscape needs {MIN_LANDSCAPE_ARCHETYPES}",
            found=len(plottable),
        )
    return Series(cells=cells)


def _event_dates(conn: ladybug.Connection) -> dict[str, datetime]:
    """Every event's registration date: the earliest ``createdAt`` across its field.

    One date per event rather than per deck, so both sides of a shared event sit at
    the same x (:class:`ArchetypeTimelinePoint` says why that matters). Taken over the
    whole field, not over the archetype's own decks, so an archetype that registered
    late does not shift the event it attended.
    """
    return dict(rows(conn.execute(
        "MATCH (f:Deck)-[:PLAYED_AT]->(e:Event) RETURN e.event, min(f.createdAt)"
    )))


def _archetype_events(
    conn: ladybug.Connection, tag: str, skip: list[str]
) -> tuple[set[str], dict[str, tuple[float, int]]]:
    """One archetype's events: the ones it attended, and its mean finish at each.

    Two queries, because they count different things. The attended set is every event
    the archetype's decks played at, scored or not, which is the archetype's history;
    the mean is over its **ranked** decks alone, so a deck the source never scored
    neither shifts the mean nor pads the count beside it. An event in the first and
    not the second is an attendance with no known finish, which the caller draws as a
    break rather than as an absence.

    ``skip`` leaves an event out of **both**, which is the one place this filter is not
    a null: an event that published a bracket rather than a field is worth no point at
    all here (ADR 0022), not a break. A break is the drawing of "they turned up and the
    source scored none of it", and at a bracket the source scored a good deal of it and
    none of it is readable, so the event is not the archetype's history to draw.
    """
    attended = {event for (event,) in rows(conn.execute(
        """MATCH (d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->(:Archetype {tag: $tag}),
                 (d)-[:PLAYED_AT]->(e:Event)
           WHERE NOT e.event IN $skip
           RETURN DISTINCT e.event""",
        {"tag": tag, "skip": skip},
    ))}
    # `avg()` and `count()` share this projection, which issue #151's note says to split
    # into two queries. That note carries the superseded diagnosis (see
    # `archetype_landscape`): the rule the engine actually holds to is that a
    # `count(DISTINCT ...)` must come **last** in a grouped projection, and this count
    # is not distinct (the match yields one row per deck, so the decks are the rows).
    # Measured over all 121 drawable archetypes on the current artifact, no mean or
    # count comes back NaN or zero here.
    scored = {
        event: (mean, decks)
        for event, mean, decks in rows(conn.execute(
            """MATCH (d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->(:Archetype {tag: $tag}),
                     (d)-[:PLAYED_AT]->(e:Event)
               WHERE d.placementNorm IS NOT NULL AND NOT e.event IN $skip
               RETURN e.event, avg(d.placementNorm), count(d)""",
            {"tag": tag, "skip": skip},
        ))
    }
    return attended, scored


def comparable_points(
    cells: list[ArchetypeTimelinePoint], paired: bool
) -> list[ArchetypeTimelinePoint]:
    """The timeline points that carry a finish on every side the reading needs.

    An archetype's point is a mean over whichever of its decks the source scored, so a
    point can be an attendance with no value on one side or the other. Those are drawn
    (the line breaks over them) but cannot be counted: they are neither a position on
    the axis nor, for a pair, a comparison. ``paired`` says whether the second side is
    part of the reading.

    One definition, two readers, which is why it is a function rather than a
    comprehension in each: :func:`archetype_timeline` floors on this count
    (:data:`MIN_ARCHETYPE_EVENTS`) and the app's headline states a win count out of it,
    so a drift between them would print a denominator the refusal does not use.
    """
    return [
        c for c in cells
        if c.mean_norm_a is not None and (not paired or c.mean_norm_b is not None)
    ]


def beats_a_coin(led: int, of: int) -> bool:
    """Whether leading ``led`` of ``of`` events is a lead chance would not have handed out.

    The sign test the archetype timeline's headline needs before it can be read as a
    lead (issue #175). Each of its points is the mean of a median of **one** ranked
    deck, so under the null "this archetype is the middle of the field" every event is a
    coin flip, and a count out of a handful of them is not evidence on its own: run over
    the 121 archetypes the surface offers, 102 of the headline counts are ones a fair
    coin produces at least 10% of the time, "8 of 14" (about 40%) among them.

    True where the count sits further than :data:`INTERVAL_Z` standard errors from the
    half the null predicts, the same 90% every other claim in this module is settled at,
    on the binomial's ``sqrt(of) / 2``. Public, and beside the tool rather than in the
    app, for the reason :func:`comparable_points` is: the count and the gate on it have
    to be the same arithmetic, and this is the module that owns what the points mean.

    **The half-step is a continuity correction, and it is load-bearing rather than a
    refinement.** A normal cut approximates a discrete count, and every disagreement it
    has with the exact binomial here falls the unsafe way. Without the correction the
    gate certified 52 distinct counts at ``of`` up to 60 whose exact two-sided p is 0.10
    or worse, and they are not exotic: ``MIN_ARCHETYPE_EVENTS`` is 2, so "3 of 3" is
    drawable and was passed as a lead though a coin produces it a quarter of the time.
    On the current artifact it wrongly certified 10 of the 121 headlines, "Golgari
    Cradle 7 of 9" (p = 0.18) and three unanimous four-event runs (p = 0.125) among
    them. With the
    correction that sweep certifies nothing at p >= 0.10, and what it newly hedges is
    only ever a claim the exact test also refuses.

    A tie is counted in ``of`` by the caller and never in ``led``, which pulls the null's
    centre above the highest count either side can reach and so only makes the gate
    harder to clear. That is the safe direction, and it keeps the tested denominator the
    same one the headline prints, so the sentence a reader checks is the sentence the
    arithmetic ran on.
    """
    return abs(led - of / 2) - 0.5 > INTERVAL_Z * math.sqrt(of) / 2


def archetype_timeline(
    conn: ladybug.Connection, a: str, b: str | None = None
) -> Series:
    """One archetype's finish over time, or two archetypes' over their shared events.

    With one archetype, a point per event it attended: the mean ``placementNorm`` of
    its ranked decks there, left raw (0 a win) as the rest of the module leaves it, the
    decks that mean rests on beside it, and the event's registration date as the x
    (:class:`ArchetypeTimelinePoint`). With two, the points are restricted to the events
    **both** attended and each carries both sides. That restriction visibly reshapes the
    first archetype's line (Grixis attended 74 events and Jund 61, but they shared 55),
    which is why the surface states it rather than leaving the shift to be read as a
    glitch.

    **Attending both is not being scored at both, so a drawn point can still be
    one-sided.** An event that published part of its field can rank one archetype's
    decks and none of the other's, and that point is drawn with a break on the
    unscored side, exactly as the solo line breaks (:class:`ArchetypeTimelinePoint`,
    :func:`archetypes_with_history`). Measured over the 4,888 pairs this surface will
    draw, 84 of them (1.7%) hold such a point, and every one of the 84 traces to the
    single event that published a thinned standings rather than a bracket
    (``GGWAD``, 16 finishes of 28, kept by :data:`MIN_FIELD_COVERAGE`). On those pairs
    the headline's denominator sits one below the marks on the axis, since it counts
    :func:`comparable_points` and the axis carries the attendance: ``breachbond`` and
    ``jund`` draw 18 points under "11 of 17 shared events".

    An event that published a bracket rather than a field is worth no point on either
    line (:func:`_cut_only_events`, ADR 0022). A point here is a mean over the decks of
    one archetype at one event, and at a bracket those are the decks that cut, so the
    point would sit near the top of the axis whatever the archetype did. It is dropped
    rather than broken over, since a break says the source scored none of it.

    An archetype has no rivalry with itself, so ``a == b`` is refused here rather than
    drawing every event twice as two identical sides. As in
    :func:`head_to_head_timeline`, the guard lives in the tool because the tool is the
    seam an agent reaches without the app's own distinct-archetype check, and it raises
    a plain ``ValueError`` rather than :class:`NotEnoughHistory`, since a malformed
    question is not a thin answer. Asking for one archetype is spelled ``b=None``.
    """
    if a == b:
        raise ValueError(f"{a} has no rivalry with itself; pick one archetype or two")
    dates = _event_dates(conn)
    skip = _cut_only_events(conn)
    attended_a, scored_a = _archetype_events(conn, a, skip)
    attended_b, scored_b = _archetype_events(conn, b, skip) if b else (None, {})
    events = attended_a if attended_b is None else attended_a & attended_b
    points = []
    for event in sorted(events, key=lambda e: (dates[e], e)):
        mean_a, decks_a = scored_a.get(event, (None, 0))
        mean_b, decks_b = scored_b.get(event, (None, 0))
        points.append(ArchetypeTimelinePoint(
            event=event, date=dates[event],
            mean_norm_a=mean_a, decks_a=decks_a,
            mean_norm_b=mean_b, decks_b=decks_b,
        ))
    # The floor is on the points that can be compared, not on the events attended
    # (see :data:`MIN_ARCHETYPE_EVENTS`).
    comparable = comparable_points(points, paired=b is not None)
    if len(comparable) < MIN_ARCHETYPE_EVENTS:
        subject = f"{a} and {b} were both ranked at" if b else f"{a} was ranked at"
        raise NotEnoughHistory(
            f"{subject} {len(comparable)} event(s); a timeline needs "
            f"{MIN_ARCHETYPE_EVENTS}",
            found=len(comparable),
        )
    return Series(cells=points)


def meta_share_over_time(conn: ladybug.Connection) -> Series:
    """Each archetype's share of the meta per year: the full matrix, no truncation.

    Decks are grouped by their primary archetype and their event's year (via the
    ``IN_YEAR`` edge). The share's base is every deck that year, so a year's shares
    sum to one only where every deck carries a primary archetype; a deck without
    one is left uncounted in the numerators and dilutes the shares honestly rather
    than inflating them. The full ``(archetype, year, share, n)`` matrix is returned,
    every cell carrying its year's total N and its share, which is never withheld:
    a share is a direct observation, so a two-deck year states the 0.21% it is
    rather than a hole the eye reads as a zero (ADR 0013). Thin years are kept
    whole, since a coarse year is honest as long as its N is visible.

    The matrix is rectangular: every archetype gets a cell in every year of the
    graph, so an ``(archetype, year)`` pair with no decks comes back a real zero
    rather than a missing row, exactly as ``card_adoption`` fills a year a card sat
    out. A line then drops to zero across a year its archetype was absent instead
    of jumping the gap and reading as continuous presence. The trend tab, not this
    tool, decides which of the ~125 archetypes to draw.

    The archetypes are read from the ``Archetype`` nodes, not from the rows the
    primary-archetype join returned, so an archetype no deck ever carried as its
    primary is a line of real zeros rather than no line at all. Reading them off the
    join made the matrix rectangular only over the archetypes that happened to appear
    in it, which is the same hole ``card_adoption`` avoids by filling every year for a
    card the graph does not hold (issue #101).
    """
    year_total = _year_totals(conn)
    names = dict(rows(conn.execute("MATCH (a:Archetype) RETURN a.tag, a.name")))
    counts: dict[tuple[str, int], int] = {}
    for tag, year, n in rows(conn.execute(
        """MATCH (d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype),
                 (d)-[:PLAYED_AT]->(:Event)-[:IN_YEAR]->(y:Year)
           RETURN a.tag, y.year, count(DISTINCT d)"""
    )):
        counts[(tag, year)] = n
    cells = []
    years = sorted(year_total.items())
    for tag, name in sorted(names.items()):
        for year, total in years:
            n = counts.get((tag, year), 0)
            cells.append(SeriesCell(
                tag=tag,
                archetype=name,
                year=year,
                n=n,
                share=n / total,
                year_total=total,
            ))
    return Series(cells=cells)


def card_adoption_over_time(
    conn: ladybug.Connection, canon: str, board: str | None = None
) -> Series:
    """One card's adoption per year: decks running it, its share, and the year base.

    Decks running the card are grouped by their event's year via the ``IN_YEAR``
    edge and counted. Adoption carries no floor, the same as ``meta_share``: both
    are direct observations, so a low count is the signal of a card entering the
    format, not noise to gap (ADR 0013). Every year in the graph gets a cell, so a year the
    card sat out comes back a real ``count`` of zero rather than a missing row, and
    each cell carries its year's total decks so a thin year's small count is not
    misread against a fat year's. A card absent from the whole graph returns a zero
    in every year, never an empty series.

    ``board`` scopes the numerator only: ``None`` counts a deck running the card in
    either board, ``"Main"`` or ``"Side"`` only the decks running it there. The year
    base is always every deck that year, so the board filter narrows what counts as
    adoption without narrowing what it is a share of.
    """
    where = "WHERE cont.board = $board" if board else ""
    params = {"canon": canon, "board": board} if board else {"canon": canon}
    adoption = dict(rows(conn.execute(
        f"""MATCH (:Card {{canon: $canon}})<-[cont:CONTAINS]-(d:Deck)
                  -[:PLAYED_AT]->(:Event)-[:IN_YEAR]->(y:Year)
           {where}
           RETURN y.year, count(DISTINCT d)""",
        params,
    )))
    year_total = _year_totals(conn)
    cells = []
    for year, total in sorted(year_total.items()):
        count = adoption.get(year, 0)
        cells.append(
            AdoptionCell(year=year, count=count, share=count / total, year_total=total)
        )
    return Series(cells=cells)


def _cut_only_events(conn: ladybug.Connection) -> list[str]:
    """Events that published a top cut instead of a field, worth no finish to anybody.

    Fewer than :data:`MIN_FIELD_COVERAGE` of the field on record, which on this corpus
    is a published bracket every time: the only finishes that exist are the good ones,
    and everyone who attended and busted is missing. A mean over those is a mean over
    results selected on their own answer, so the events are dropped from every surface
    that reads a *level* off a finish rather than discounted, there being no way to
    infer how many bad finishes were withheld. That is the race and the performance
    chart for a pilot's level (ADR 0021), and the landscape's finish axis, the archetype
    timeline and the catalogue over it for an archetype's (ADR 0022).

    Returned sorted, as a list rather than a set, because it is passed straight to a
    query as a parameter and a set has no stable order to bind.

    Not every surface wants this. The hidden gem rule reads the same events and must
    keep them: its null is conditioned on each event's own cut (``query._gem_tails``),
    so a bracket is compared against a bracket's hit rate rather than the field's, which
    uses the information instead of discarding it. Nor does a **count** of decks want
    it: meta share, the landscape's share axis and card adoption count every deck the
    source shipped, a bracket's included, because those decks were genuinely played and
    the bias there is 0.14pp at worst (ADR 0022).
    """
    return sorted(
        event
        for event, field, ranked in rows(conn.execute(
            "MATCH (d:Deck)-[:PLAYED_AT]->(e:Event) WHERE d.placementNorm IS NOT NULL "
            "RETURN e.event, e.fieldSize, count(d)"
        ))
        if field is not None and ranked < field * MIN_FIELD_COVERAGE
    )


def _within_pilot_sd(conn: ladybug.Connection, skip: list[str]) -> float | None:
    """The field's within-pilot spread: how far one finish bounces from a pilot's level.

    The pilot chart's fit for :func:`_interval`, taken over every pilot carrying
    :data:`MIN_SPREAD_FINISHES` ranked finishes and over their whole career rather than
    per year, since it is the pilot's own level a finish bounces around and a year is
    too short to see it. It is fitted over the same finishes the chart draws, so a
    bracket-only event is no part of it either (:func:`_cut_only_events`): the spread
    wanted is of the quantity being plotted. On the current artifact that is 257 pilots,
    3,003 finishes and a pooled sd of 0.266, so a two-event year earns a half-width of
    0.31 and a twenty-event one 0.10.

    That 0.266 runs a little above the 0.2565 issue #175's coverage simulation was
    fitted on: :func:`_pooled_sd` divides by the degrees of freedom where the ticket's
    residual figure divided by the count, and the brackets the ticket still counted have
    since come out. The difference is 3.7% of the width, it goes the conservative way (a
    wider interval covers more, and the simulation already cleared 90% at the narrower
    fit), and the unbiased form is kept because the quantity wanted here is the field's
    spread rather than this sample's.

    It is a property of the field, so it is the same for every pilot and every year, and
    a pilot's own record widens nobody's interval but their peers' by joining the pool.
    Records are walked in pilot order and each is sorted inside :func:`_pooled_sd`, so
    the fit does not move with Ladybug's row order between calls.
    """
    careers: dict[str, list[float]] = {}
    for pilot, norm in rows(conn.execute(
        """MATCH (p:Pilot)<-[:PILOTED_BY]-(d:Deck)-[:PLAYED_AT]->(e:Event)
           WHERE d.placementNorm IS NOT NULL AND NOT e.event IN $skip
           RETURN p.pilot, d.placementNorm""",
        {"skip": skip},
    )):
        careers.setdefault(pilot, []).append(norm)
    return _pooled_sd([
        career for _, career in sorted(careers.items())
        if len(career) >= MIN_SPREAD_FINISHES
    ])


def pilot_performance_over_time(conn: ladybug.Connection, pilot: str) -> Series:
    """One pilot's mean ``placementNorm`` per year, for years with real history.

    The pilot's decks are grouped by their event's year via the ``IN_YEAR`` edge and
    each year's mean finish taken over that year's **ranked** decks (a null
    ``placementNorm`` is a finish no rule could recover a rank for, left out so it
    neither shifts the mean nor pads the event count). ``placementNorm`` is an
    aggregate, so it carries a floor (ADR 0013): a year below
    :data:`MIN_PILOT_YEAR_EVENTS` distinct events has its mean refused, too thin to be
    honest. The floor counts events, not decks, so a list reused across events counts
    as the several finishes it is.

    The refused year is still returned, as a cell whose ``mean_norm`` is ``None``
    carrying the event count that refused it. Dropping the row instead made the
    refusal indistinguishable from a year the pilot did not play, and because a thin
    year is overwhelmingly a pilot's first or last, the drop deleted exactly the
    arrival and departure a career chart exists to show (issue #101, the same defect
    the ADR 0013 amendment found in ``meta_share``). So the series is rectangular over
    the years the pilot **played**, the way ``meta_share`` and ``card_adoption`` are
    rectangular over the graph's years; a year the pilot sat out entirely has no cell,
    because it is not their history.

    Played, not scored: a year whose decks the source never placed at all comes back
    as a cell of ``events`` zero and no mean, not as no cell. Filtering the years by
    the same null test that filters the decks would truncate a career exactly as the
    thin-year drop did. **The population that motivated this is gone, and the rule
    stays.** It was measured at six drawable pilots holding a wholly unscored year, in
    all six their first; minting norms for every recoverable rank (issue #162) leaves
    zero, because an unscored year was overwhelmingly one whose ranks sat in the
    titles unnormalised. The shape is still the right one: it is the same
    rectangular-over-the-years-played contract ``meta_share`` and ``card_adoption``
    keep, it costs nothing while the population is empty, and the population returns
    with the first snapshot whose titles carry no rank at all (24 such decks exist
    today, they simply do not fill anybody's whole year). ``events`` counts the scored
    events the mean rests on, so zero is its honest value for such a year, and the
    year still holds its place on the axis.

    :data:`MIN_QUALIFYING_YEARS` still counts only the years whose mean survived, so
    a pilot short of two of them raises :class:`NotEnoughHistory` rather than drawing
    a lone point on an empty line. Cells are ordered by year; the connecting line
    asserts no direction, only joins the points.

    Every year that keeps its mean also carries the 90% interval on it, and that is what
    the surface is for now. The movement between these means was measured against the
    artifact and is not real: shuffling each pilot's finishes across their own years
    moves the drawn line as far as their real career does (issue #175), so the value and
    its width are what a reader can take. The width comes from the **field's**
    within-pilot spread (:func:`_within_pilot_sd`), not from the year's own two or three
    finishes, which is one extra query and the only honest way to width a mean this
    thin: the year's own finishes cannot show the spread they were drawn from.
    """
    # Read once and threaded through: the mean and the spread it is widthed by both need
    # it, and it is a grouped scan of every ranked deck on a per-request path.
    skip = _cut_only_events(conn)
    # The mean and its sample, over the ranked decks only: a null placementNorm is a
    # finish no rule could rank, so it neither shifts the mean nor pads the count. Nor
    # does a finish at an event that published only its bracket, where a recorded finish
    # is a good one by construction (:func:`_cut_only_events`).
    scored = {
        year: (mean, events)
        for year, mean, events in rows(conn.execute(
            """MATCH (:Pilot {pilot: $pilot})<-[:PILOTED_BY]-(d:Deck)
                     -[:PLAYED_AT]->(e:Event)-[:IN_YEAR]->(y:Year)
               WHERE d.placementNorm IS NOT NULL AND NOT e.event IN $skip
               RETURN y.year, avg(d.placementNorm), count(DISTINCT e)""",
            {"pilot": pilot, "skip": skip},
        ))
    }
    # The years are taken without that filter, so a year the source scored none of
    # keeps its place rather than being cut off the end of the career.
    played = sorted(year for (year,) in rows(conn.execute(
        """MATCH (:Pilot {pilot: $pilot})<-[:PILOTED_BY]-(:Deck)
                 -[:PLAYED_AT]->(:Event)-[:IN_YEAR]->(y:Year)
           RETURN DISTINCT y.year""",
        {"pilot": pilot},
    )))
    # One fit for the whole chart, read off the field rather than off this pilot.
    sd = _within_pilot_sd(conn, skip)
    cells = []
    for year in played:
        mean, events = scored.get(year, (None, 0))
        # The floor is on the mean, not on the year: a thin year states its
        # sample size and withholds only the value that sample cannot carry.
        mean = mean if events >= MIN_PILOT_YEAR_EVENTS else None
        bounds = _interval(mean, events, sd) if mean is not None else None
        low, high = bounds or (None, None)
        cells.append(PerformanceCell(
            year=year, mean_norm=mean, events=events, mean_low=low, mean_high=high,
        ))
    qualifying = [cell for cell in cells if cell.mean_norm is not None]
    if len(qualifying) < MIN_QUALIFYING_YEARS:
        raise NotEnoughHistory(
            f"{pilot} has {len(qualifying)} year(s) of at least "
            f"{MIN_PILOT_YEAR_EVENTS} scored events; a trajectory needs "
            f"{MIN_QUALIFYING_YEARS}",
            found=len(qualifying),
        )
    return Series(cells=cells)


def head_to_head_timeline(conn: ladybug.Connection, a: str, b: str) -> Series:
    """Two pilots' rivalry over their shared events, one row per shared event.

    A shared event is one both pilots entered. Each row carries the event's field
    size and registration date (the earliest ``createdAt`` across the event's whole
    field, ADR 0006's proxy at day granularity, so both pilots' points share one x)
    and each pilot's raw placement and ``placementNorm``. This is the only trend to
    read the per-deck ``createdAt`` rather than group by the ``Year`` node (ADR
    0013): its x-axis needs a coordinate finer than year, or two events shared in
    one year collapse onto the same x.

    The rows are direct observations, so they carry no within-point floor; the floor
    is on the pair. A pair sharing fewer than :data:`MIN_SHARED_EVENTS` events is a
    dot, not a timeline, so it raises :class:`NotEnoughHistory` naming the number of
    events it did find, rather than coming back empty: an empty series read the same
    for a pair who met once and a pair who never met, which made the refusal
    indistinguishable from the fact (issue #101). Rows are ordered by date, the
    x-axis order; the connecting line asserts no direction, only joins the points.

    A pilot has no rivalry with themselves, so ``a == b`` is refused here rather than
    matching every event the pilot played to itself and drawing two identical lines.
    The guard lives in the tool, not only the app, because the tool is the seam an
    agent reaches without the UI's distinct-pilot check. It raises a plain
    ``ValueError`` rather than :class:`NotEnoughHistory`, because a pilot compared to
    themselves is a malformed question, not a thin answer: reporting it as no history
    would tell the caller these two never met, which is the conflation the typed
    refusal exists to end.
    """
    if a == b:
        raise ValueError(f"{a} has no rivalry with themselves; pick two pilots")
    points = sorted(
        (
            HeadToHeadPoint(
                event=event,
                date=date,
                field_size=field_size,
                placement_a=placement_a,
                norm_a=norm_a,
                placement_b=placement_b,
                norm_b=norm_b,
                field_imputed=field_imputed,
                placement_imputed_a=placement_imputed_a,
                norm_imputed_a=norm_imputed_a,
                placement_imputed_b=placement_imputed_b,
                norm_imputed_b=norm_imputed_b,
            )
            for (event, date, field_size, field_imputed,
                 placement_a, norm_a, placement_imputed_a, norm_imputed_a,
                 placement_b, norm_b, placement_imputed_b, norm_imputed_b)
            in rows(conn.execute(
                # field_size is read off the Event node, which is where the build
                # stores the field it normalises against (`build.corrected_field`,
                # ADR 0015). It used to be recovered by inverting a norm, which this
                # replaces: the inversion could not read a field an event's only
                # ranked deck won (0 is uninvertible) and fell back to the deck
                # count, a different quantity that disagreed with the stored field at
                # 3 of 107 events. That was harmless only while those events drew no
                # markers, and minting their norms is what would have made them draw
                # a win labelled "1st of 7" against a field of 24 (issue #162). The
                # `f` decks are still matched, for the event's registration date:
                # the earliest createdAt across its whole field, so both pilots' points
                # share one x (see HeadToHeadPoint above).
                """MATCH (:Pilot {pilot: $a})<-[:PILOTED_BY]-(da:Deck)
                         -[:PLAYED_AT]->(e:Event),
                         (:Pilot {pilot: $b})<-[:PILOTED_BY]-(db:Deck)
                         -[:PLAYED_AT]->(e),
                         (f:Deck)-[:PLAYED_AT]->(e)
                   RETURN e.event, min(f.createdAt), e.fieldSize, e.fieldImputed,
                          da.placement, da.placementNorm,
                          da.placementImputed, da.normImputed,
                          db.placement, db.placementNorm,
                          db.placementImputed, db.normImputed""",
                {"a": a, "b": b},
            ))
        ),
        key=lambda p: p.date,
    )
    if len(points) < MIN_SHARED_EVENTS:
        raise NotEnoughHistory(
            f"{a} and {b} share {len(points)} event(s); "
            f"a rivalry to trace over time needs {MIN_SHARED_EVENTS}",
            found=len(points),
        )
    return Series(cells=points)


def _months_before(when: datetime, months: int) -> datetime:
    """``when`` moved back whole calendar months, clamped to the month's last day.

    Month arithmetic rather than a fixed number of days, so every window spans the same
    number of whole months whichever months those are, and a boundary keeps the anchor's
    day of the month wherever that day exists (the 31st of a 30-day month clamps back).
    Windows therefore differ by a few days in length, 546 to 550 on the current anchor,
    which is what "the same length" means on a calendar and is well inside a window whose
    job is to hold a handful of events.
    """
    year, month = divmod(when.year * 12 + when.month - 1 - months, 12)
    day = min(when.day, calendar.monthrange(year, month + 1)[1])
    return when.replace(year=year, month=month + 1, day=day)


def _race_sample_dates(anchor: datetime) -> list[datetime]:
    """The race's sample dates, oldest first, the newest at ``anchor``.

    :data:`RACE_POINTS` dates stepped :data:`RACE_STEP_MONTHS` apart. They carry no
    length, because a point counts everything up to its date rather than a span ending
    at it: what a date picks out is a moment to read the standings at, and the only
    thing the step decides is how often the chart reads them.
    """
    return [
        _months_before(anchor, RACE_STEP_MONTHS * back)
        for back in reversed(range(RACE_POINTS))
    ]


def _major_finishes(
    conn: ladybug.Connection,
) -> tuple[dict[str, list[tuple[datetime, float]]], int, datetime | None]:
    """Every pilot's major finishes, the graph's major count, and the anchor date.

    A finish is one **event**, not one deck: the pilot's norms at an event are averaged
    into a single value first, so a pilot who somehow registered twice at one event is
    one independent trial and their majors count can never exceed the events their
    score rests on (the event is the honest unit here for the same reason
    :data:`MIN_PILOT_YEAR_EVENTS` counts events). It is flipped to higher-is-better on
    the way out, unlike every other tool in this module, because the race's whole
    output is a *ranking*: a raw ``placementNorm`` would leave the tool's own
    leaderboard and rank column reading upside down for the agent that consumes them,
    which is a worse trade than the flip-at-the-chart convention buys.

    The anchor is the newest event in the graph rather than the newest major, since it
    is answering "how current is this graph", and it is dated the way every other
    per-event date in this module is (the earliest ``createdAt`` across the event's whole
    field), so an event sits wholly inside a window or wholly outside it. ``None`` for a
    graph holding no event at all, which still has to build.
    """
    events = {
        event: (date, field)
        for event, date, field in rows(conn.execute(
            "MATCH (f:Deck)-[:PLAYED_AT]->(e:Event) RETURN e.event, min(f.createdAt), e.fieldSize"
        ))
    }
    # A null field is no major, which is what the query below already does with it (a
    # NULL fails `fieldSize > $major`), so the two halves have to agree: comparing a
    # null here raises, and nothing above catches it, so it would take the whole app
    # down rather than this one tab. The build fills a missing size only for a
    # ``Tournament`` (``corrected_field``'s Rule C), so a non-Tournament event the
    # source ships without one reaches the graph with a null.
    #
    # A major also has to have published its field and not just its bracket, which is
    # what the second test is: a score is a claim about a pilot's level, and a bracket
    # only records the pilots who did well (:func:`_cut_only_events`). It costs the
    # current graph 2 of 21 majors.
    skip = set(_cut_only_events(conn))
    majors = {
        e for e, (_, field) in events.items()
        if field is not None and field > MAJOR_FIELD_SIZE and e not in skip
    }
    finishes: dict[str, list[tuple[datetime, float]]] = {}
    for pilot, event, norm in rows(conn.execute(
        """MATCH (p:Pilot)<-[:PILOTED_BY]-(d:Deck)-[:PLAYED_AT]->(e:Event)
           WHERE d.placementNorm IS NOT NULL AND e.fieldSize > $major
           RETURN p.pilot, e.event, avg(d.placementNorm)""",
        {"major": MAJOR_FIELD_SIZE},
    )):
        if event in majors:
            finishes.setdefault(pilot, []).append((events[event][0], 1 - norm))
    anchor = max((date for date, _ in events.values()), default=None)
    # Each record in date order, which the query's own row order does not give. Two calls
    # on one graph can hand these rows back in different orders, and every consumer here
    # sums a record (the shrinkage, the score, each running point), so an unsorted record
    # makes the same graph score fractionally differently run to run. Sorting once here
    # is what lets `player_leaderboard` promise the same numbers twice.
    return (
        {pilot: sorted(record) for pilot, record in sorted(finishes.items())},
        len(majors),
        anchor,
    )


def _shrinkage(records: list[list[float]]) -> tuple[float, float | None]:
    """The field average and the shrinkage weight, estimated from the field itself.

    ``mu`` is the field average, the value a record with nothing behind it is assumed
    to be until its own evidence says otherwise. ``k`` is how many majors of evidence
    that assumption is worth: the ratio of the variance *within* a pilot's record (how
    far one finish bounces from their own level) to the variance *between* pilots (how
    far pilots' levels really differ). Both are estimated at build time and neither is
    ever a constant, because both are properties of this record: a source that placed
    finishes more finely, or a field that spread out, changes what a thin record is
    worth and the number has to follow it.

    The estimator is the textbook one-way random-effects decomposition: pool the
    within-record variance, take the spread of record means against it, and read the
    difference as the real spread of pilots. It runs over the contenders alone, the
    population being ranked, each of whom carries at least
    :data:`MIN_CAREER_MAJORS` finishes, so the within-record term is estimated on
    records long enough to have one.

    ``k`` comes back ``None`` where the between-pilot term lands at or below zero,
    which says the field's spread is no larger than the bounce inside one record: on
    that evidence no pilot is distinguishable from the field, so every score is the
    field average and the caller draws a race with nothing between its runners. The
    real record is nowhere near it (within 0.066 against between 0.013, so k is about
    5, meaning a pilot needs 4 or 5 majors before their own record outweighs simply
    knowing they qualified), but a young graph can be.
    """
    pilots = len(records)
    observations = [finish for record in records for finish in record]
    mu = sum(observations) / len(observations)
    means = [sum(record) / len(record) for record in records]
    within = sum(
        (finish - mean) ** 2
        for record, mean in zip(records, means)
        for finish in record
    ) / (len(observations) - pilots)
    across = sum(
        len(record) * (mean - mu) ** 2 for record, mean in zip(records, means)
    ) / (pilots - 1)
    # The mean record length the two mean squares are comparable at; it is not simply
    # the average length, because a long record contributes more to the spread of
    # means than a short one does.
    typical = (
        len(observations) - sum(len(r) ** 2 for r in records) / len(observations)
    ) / (pilots - 1)
    between = (across - within) / typical
    return mu, within / between if between > 0 else None


def _shrunk(finishes: list[float], mu: float, k: float | None) -> float:
    """A mean pulled toward the field average by how little evidence stands behind it.

    ``(n * mean + k * mu) / (n + k)``: the record's own mean weighted by its length
    against the field average weighted by :func:`_shrinkage`'s ``k``. A record the
    length of ``k`` is scored half on itself; a record twice that, two thirds. With no
    separable field (``k`` is ``None``) every record scores the field average.
    """
    if k is None:
        return mu
    return (sum(finishes) + k * mu) / (len(finishes) + k)


def _standings(scores: dict[str, float]) -> dict[str, int]:
    """Standings over a set of scores, best first: rank 1 is the highest.

    Used for a sample date, for the career, and for a resampled field alike. Only the
    pilots given a score are ranked, so a date's rank is always out of the contenders
    who had a record by then. Equal scores share the better rank, since the order
    between them is the sort's, not the record's.
    """
    standings: dict[str, int] = {}
    rank, previous = 0, None
    for position, (pilot, score) in enumerate(
        sorted(scores.items(), key=lambda entry: -entry[1]), start=1
    ):
        if score != previous:
            rank, previous = position, score
        standings[pilot] = rank
    return standings


def _rank_intervals(records: dict[str, list[float]]) -> dict[str, tuple[int, int]]:
    """Each contender's career rank resampled: the :data:`RACE_INTERVAL` bounds on it.

    The nonparametric bootstrap. Every contender's finishes are redrawn with replacement
    to their own length, the whole field is rescored, and the standings are taken again;
    the bounds are the percentiles of where a pilot landed across
    :data:`RACE_RESAMPLES` of those. Redrawing the whole field at once rather than one
    pilot at a time is what makes a rank interval a rank interval: a rank is a
    statement about a pilot *against the others*, so the others have to move too. The
    shrinkage is re-estimated inside the loop for the same reason, since ``mu`` and
    ``k`` are properties of the field and a resampled field is a different one.

    It answers the question a score cannot: how much of this ordering is the record and
    how much is which events happened to fall in it. Seeded from
    :data:`RACE_RESAMPLE_SEED`, so a rebuild of the same graph draws the same interval
    and a moved bound means moved evidence.

    What it assumes, and it is worth naming because it widens the bounds: a pilot's
    finishes are draws from one fixed level, so a pilot who genuinely improved has that
    improvement counted as noise. The interval is therefore conservative. It is not
    conservative enough to matter at the top of this board, where the alternative
    reading would have to turn a coin flip into a certainty.
    """
    # Walked in pilot order, not the order `records` happens to hold. The seed alone does
    # not make this reproducible: the dict comes off a query whose row order Ladybug does
    # not guarantee between calls, so an unsorted walk hands the same seeded stream to a
    # different pilot each run and two calls on one graph disagree. The tool already
    # settles cell order for this reason; this is the same hazard one level down, and it
    # is the sort that fixes it rather than the seed.
    field = sorted(records.items())
    rng = random.Random(RACE_RESAMPLE_SEED)
    placings: dict[str, list[int]] = {pilot: [] for pilot in records}
    for _ in range(RACE_RESAMPLES):
        drawn = {
            pilot: [finishes[rng.randrange(len(finishes))] for _ in finishes]
            for pilot, finishes in field
        }
        mu, k = _shrinkage(list(drawn.values()))
        for pilot, rank in _standings(
            {pilot: _shrunk(finishes, mu, k) for pilot, finishes in drawn.items()}
        ).items():
            placings[pilot].append(rank)
    # Symmetric tails, so the two bounds are cut the same distance in: an interval that
    # took `int(0.95 * n)` for the high bound and `int(0.05 * n)` for the low one would
    # sit a resample wider at the top than at the bottom for no reason a reader could see.
    low = int((1 - RACE_INTERVAL) / 2 * RACE_RESAMPLES)
    return {
        pilot: (sorted(ranks)[low], sorted(ranks)[RACE_RESAMPLES - 1 - low])
        for pilot, ranks in placings.items()
    }


def player_leaderboard(conn: ladybug.Connection) -> Series:
    """The field's leading pilots, traced by what their record said as it came in.

    One cell per contender per sample date (:class:`RaceCell`), the whole field and the
    whole span: which of them the chart draws as lines and how many the leaderboard
    lists are display cuts the app applies, so the agent always receives the full table
    (ADR 0013's rule for ``meta_share``).

    A contender is a pilot with :data:`MIN_CAREER_MAJORS` majors behind them and
    :data:`MIN_RECENT_MAJORS` of them inside the last :data:`RACE_RECENCY_MONTHS`, both
    measured against the newest event in the graph rather than a calendar date, so the
    gates move forward with the record instead of quietly retiring the field as an
    artifact ages.

    The trajectory is a **running** score, not a rolling one (ADR 0017): a point counts
    every major up to its date, so the series traces the accumulation of evidence about
    a pilot rather than a claim about their form. Two consequences worth expecting. The
    last point of every line is that pilot's career score by construction, so the
    standings at the right edge are exactly the leaderboard's, and a line that starts
    low is a thin record being held near the field average rather than a pilot who was
    once bad. The rolling version this replaces claimed the second reading and could not
    support it: its movement measured as three percent signal (see
    :data:`RACE_STEP_MONTHS`).
    """
    finishes, major_events, anchor = _major_finishes(conn)
    recency_line = _months_before(anchor, RACE_RECENCY_MONTHS) if anchor else None
    contenders = {
        pilot: majors
        for pilot, majors in finishes.items()
        if len(majors) >= MIN_CAREER_MAJORS
        and sum(1 for date, _ in majors if date > recency_line) >= MIN_RECENT_MAJORS
    }
    if len(contenders) < MIN_RACE_CONTENDERS:
        raise NotEnoughHistory(
            f"{len(contenders)} pilot(s) clear {MIN_CAREER_MAJORS} career majors with "
            f"{MIN_RECENT_MAJORS} of them recent; a race needs {MIN_RACE_CONTENDERS}",
            found=len(contenders),
        )
    records = {pilot: [s for _, s in majors] for pilot, majors in contenders.items()}
    mu, k = _shrinkage(list(records.values()))
    scores = {pilot: _shrunk(finishes, mu, k) for pilot, finishes in records.items()}
    intervals = _rank_intervals(records)
    dates = _race_sample_dates(anchor)
    so_far = [
        {
            pilot: [s for date, s in majors if date <= at]
            for pilot, majors in contenders.items()
        }
        for at in dates
    ]
    scored = [
        {
            pilot: _shrunk(finishes, mu, k)
            for pilot, finishes in step.items()
            if len(finishes) >= MIN_SCORED_MAJORS
        }
        for step in so_far
    ]
    standings = [_standings(step) for step in scored]
    career = _standings(scores)
    # Standing order, best first, each contender's dates oldest first. The query's own
    # row order is not stable between calls, and the legend, the leaderboard and the
    # colour assignment all read this order, so it is settled here rather than left to
    # each caller to re-sort. Ties break on the pilot key, the same tie-break the meta
    # cut uses, so one graph always draws one race.
    return Series(cells=[
        RaceCell(
            pilot=pilot,
            rank=career[pilot],
            score=scores[pilot],
            majors=len(contenders[pilot]),
            major_events=major_events,
            rank_low=intervals[pilot][0],
            rank_high=intervals[pilot][1],
            as_of=at,
            as_of_score=scored[step].get(pilot),
            as_of_majors=len(so_far[step][pilot]),
            as_of_rank=standings[step].get(pilot),
            as_of_contenders=len(standings[step]),
        )
        for pilot in sorted(contenders, key=lambda p: (-scores[p], p))
        for step, at in enumerate(dates)
    ])


def pilots_with_history(conn: ladybug.Connection) -> list[tuple[str, str]]:
    """``(displayName, pilot)`` for every pilot the performance trend can draw.

    A pilot qualifies exactly when :func:`pilot_performance_over_time` would return a
    trajectory: at least two years each clearing :data:`MIN_PILOT_YEAR_EVENTS` distinct
    events. The floor rule lives in both places (the same constant and the same
    two-year gate), so the trend tab offers only pilots that draw rather than letting
    a pick land on "not enough history", the way ``gem_archetypes`` offers only the
    slices the gem band can answer for. The meta-share tab has no such catalogue:
    its measure carries no floor, so every archetype draws.
    """
    return [(name, key) for name, key in rows(conn.execute(
        """MATCH (p:Pilot)<-[:PILOTED_BY]-(d:Deck)
                 -[:PLAYED_AT]->(e:Event)-[:IN_YEAR]->(y:Year)
           WHERE d.placementNorm IS NOT NULL AND NOT e.event IN $skip
           WITH p, y.year AS year, count(DISTINCT e) AS events
           WHERE events >= $floor
           WITH p, count(year) AS years
           WHERE years >= $min_years
           RETURN p.displayName, p.pilot
           ORDER BY p.displayName""",
        {"floor": MIN_PILOT_YEAR_EVENTS, "min_years": MIN_QUALIFYING_YEARS,
         "skip": _cut_only_events(conn)},
    ))]


def archetypes_with_history(conn: ladybug.Connection) -> list[tuple[str, str, int]]:
    """``(name, tag, events)`` for every archetype :func:`archetype_timeline` can draw.

    An archetype qualifies exactly when a solo timeline would return a line: at least
    :data:`MIN_ARCHETYPE_EVENTS` events it was ranked at, and ranked at an event the
    timeline can draw, so the bracket-only ones are no part of the count either
    (:func:`_cut_only_events`, ADR 0022). The same rule in two places, as
    ``pilots_with_history`` holds it for the pilot trend, so a pick from the catalogue
    never lands on a refusal.

    The count is the archetype's **scored** events, the evidence behind its line, and so
    the count the floor is taken on. It is not always the number of points the solo plot
    draws, which is every event the archetype attended: the two differ by one event at 6
    of the 121 (``boros``, ``breachbond``, ``goblins``, ``hardened_scales``, ``rogue``,
    ``shops``), where the archetype turned up and the source scored none of its decks.
    The label carries the evidence
    rather than the mark count, since what a reader is being warned about before picking
    a thin archetype is how much of it is known, not how many gaps it will draw.

    Nearly the whole catalogue qualifies (121 of 126 archetypes), which is the point:
    this plot is the granular escape hatch for everything the landscape's top 25 hides,
    so filtering it to the archetypes with a comfortable history would defeat its
    purpose and put ``golgari_cradle`` (12 decks over 9 events) out of reach anywhere on
    the tab. The event count rides along instead, for the label to carry, so a reader
    sees how thin an archetype is before picking it. Ordered by display name, the
    dropdown's order; the tag is what identifies it, since two archetypes can share a
    name.
    """
    return [(name, tag, events) for name, tag, events in rows(conn.execute(
        """MATCH (d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype),
                 (d)-[:PLAYED_AT]->(e:Event)
           WHERE d.placementNorm IS NOT NULL AND NOT e.event IN $skip
           WITH a, count(DISTINCT e) AS events
           WHERE events >= $floor
           RETURN a.name, a.tag, events
           ORDER BY a.name""",
        {"floor": MIN_ARCHETYPE_EVENTS, "skip": _cut_only_events(conn)},
    ))]


def run_series(conn: ladybug.Connection, spec: SeriesSpec) -> Series:
    """Map a series spec to its trend function: the sibling of ``run_query``.

    The single seam over the trend tools, kept apart from ``run_query`` because a
    trend is a ``Series``, not a ``Subgraph`` (ADR 0013). A new trend means a
    function, a spec dataclass, its member in ``SeriesSpec``, and a case here.
    """
    match spec:
        case MetaShareOverTime():
            return meta_share_over_time(conn)
        case CardAdoptionOverTime(canon, board):
            return card_adoption_over_time(conn, canon, board)
        case PilotPerformanceOverTime(pilot):
            return pilot_performance_over_time(conn, pilot)
        case HeadToHeadTimeline(a, b):
            return head_to_head_timeline(conn, a, b)
        case ArchetypeLandscape(year):
            return archetype_landscape(conn, year)
        case ArchetypeTimeline(a, b):
            return archetype_timeline(conn, a, b)
        case PlayerLeaderboard():
            return player_leaderboard(conn)
        case _:
            raise TypeError(f"unknown series spec: {spec!r}")


def latest_deck_year(series: Series) -> int | None:
    """The latest year the series has a deck in, or ``None`` for a series with none.

    Not simply the latest year the matrix holds: the matrix is rectangular, so a year
    whose decks all reached the graph without a primary archetype still holds a cell
    per archetype, every one of them zero. That year has nothing to rank or title a
    chart with, so the year the chart speaks for is the latest one with a deck behind
    it. Shared by the cut and the app's chart title so the two name the same year.
    """
    years = [cell.year for cell in series.cells if cell.n]
    return max(years) if years else None


def latest_year_share_cut(series: Series, cut: float = 0.50) -> list[str]:
    """The archetype tags to draw for a cumulative-share ``cut`` (default 50%).

    A display cut, not a data cut: the tool returns every archetype, but drawing
    all ~125 as lines is a hairball. The archetypes are ranked by their deck count
    in the **latest year the series holds a deck in** (whichever that is, read from
    the data rather than pinned to a year), and the strongest are kept until their
    cumulative share of that year's decks reaches ``cut``. The question the chart
    answers is "what is the meta now, and how did it get here", so today's top
    archetypes are the ones worth tracing back; a pooled all-year ranking instead lets
    a dead archetype with a fat past crowd out a live one. The set is still computed
    once, so the same lines span the whole x-axis rather than entering and leaving per
    year; the trend tab's manual panel is the escape hatch for an archetype large
    only earlier. Returned in rank order, strongest first, with archetypes on the
    same deck count ranked by ascending tag: when the cut lands inside a band of
    equal counts, that tie-break alone decides which of the equals is drawn.
    """
    latest = latest_deck_year(series)
    if latest is None:
        return []
    counts = {cell.tag: cell.n for cell in series.cells if cell.year == latest}
    total = sum(counts.values())
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    kept: list[str] = []
    cumulative = 0
    for tag, n in ranked:
        kept.append(tag)
        cumulative += n
        if cumulative / total >= cut:
            break
    return kept
