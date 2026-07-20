# Append-stable threading of a split id's decks into careers

Supersedes the placement-rank dealing in ADR 0004 and delivers the "second wave"
ADR 0005 deferred. When one upstream id fields several decks at a single event
(distinct people sharing a pseudonym, up to 8 deep), resolution splits it into
numbered people. ADR 0004 dealt the decks out by placement rank: at each event
the best-placed deck went to identity 1, the next to identity 2, and so on. This
ADR replaces that with threading the decks into careers by card-set similarity.

## The decision

A pilot's decks are grouped into careers so that alike decks share a career and
each career holds at most one deck per event (a career is one person, one entry
per event). Events are threaded oldest first (smallest deck id first, since the
source assigns ids in registration order). Overlap is the maximum card-set
Jaccard against any deck already in the career (ADR 0005's definition), so a
career's signature does not dilute as it accumulates decks.

Assigning an event's decks to careers has two cases, because the split only
deepens at a *seeding* event (one with more decks than there are careers so far):

- **Not seeding** (the decks fit inside the existing careers): decks are taken
  oldest first, each claiming its best free career. Oldest-first keeps a
  later-ingested deck append stable (see below).
- **Seeding**: the best-fitting decks claim the existing (accumulated) careers,
  and the leftover decks open the new ones. Oldest-first would instead hand an
  accumulated career to whichever colliding deck happens to sort first by id,
  stranding the deck that actually continues that history on a fresh career. A
  live example: a Walkers pilot who at one event fields both a Walkers list and a
  one-off Mardu list; oldest-first can put Mardu on the Walkers career and exile
  the Walkers deck to a new person. There is no incumbent to protect on the new
  careers, so best-fit is safe there.

The number of careers equals the pilot's deepest same-event collision: a pilot
with no collision threads into one career and stays a single node, exactly as
before. Only the assignment of overflow decks changes, not who splits.

## Why placement rank was not stable

Placement rank is not a property of a deck, it is a property of a deck relative to
the rest of the field. A later fetch that adds one registration can shuffle which
deck lands on which numbered identity, so a deck's person moved with no fact
changing about that deck. Any career keyed on the numbered identity drifted across
rebuilds. A multi-agent audit of the pipeline confirmed the drift with a live
repro (issue #34): adding a better-placed deck at an event renumbered the two
identities already there.

## Append-stable numbering

Careers are numbered by their earliest deck (smallest deck id). That anchor is
what makes threading append-stable. A newly ingested deck carries a larger id than
everything before it, so within its event it is assigned after the decks already
there: it either joins an existing career (without moving that career's anchor and
without bumping an incumbent, since the incumbents chose their careers first) or
opens a new career that sorts last. Either way it never renumbers the careers
already there, and no prior deck's career membership changes. Re-ingesting stable
input yields the same careers, and the grouping is a deterministic function of
deck ids and card sets, independent of input order.

Oldest-first at a non-seeding event is the load-bearing detail for stability. A
global best-overlap match there would let a high-overlap newcomer seize a career
an older deck already sat in, relocating that older deck with no fact changing
about it: the same drift, moved from placement rank to overlap. Assigning older
decks first closes that. The best-fit rule is confined to seeding events, where
the careers being claimed are new and hold no incumbent to displace, so it buys
correct grouping without reopening the drift.

This all rests on the source assigning deck ids in registration order, the same
monotonicity the multi-snapshot union already assumes (ADR 0003). A backfilled
historical deck with a small id would re-thread rather than append, which is
correct: it is a genuinely new fact about the past, not an append.

## What it does not decide

The numbered identity is synthetic and not dictionary-addressable, so no curation
key rests on it (ADR 0005 keys only on stable upstream ids). Switching from
placement rank to threading therefore reshuffles the current split numbering once
and loses nothing curated. Threading is over decks that already reached one
resolved id; it does not merge or split ids, which remains the curation
dictionary's job alone.

The per-event assignment is greedy (oldest-first off a seeding event, best-fit on
one). This is exact for well-separated careers (the real case)
and a close approximation when two careers share most of their cards. A global
maximum-overlap matching would score marginally higher on tangled cases but would
sacrifice append-stability (see above), so the per-deck order is chosen
deliberately, not as a shortcut. The families are small (at most 8 decks at an
event, a handful of careers) and the assignment is deterministic, so the choice is
reproducible even where it is not provably optimal.

## Consequences

`_split_event_collisions` threads via `_thread_careers` instead of dealing slots
by placement, and `_event_slots` is gone. Each split identity stays low
confidence: threading makes the grouping reproducible, not confirmed, since the
data still cannot prove two same-event decks are two people rather than one. The
reconciliation report's `event_splits` are unchanged in shape.
