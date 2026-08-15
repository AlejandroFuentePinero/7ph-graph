# 25. An upstream event merge is refused, not absorbed

Date: 2026-08-16

## Status

Accepted

## Context

On 2026-08-15 a fetch arrived in which the source had folded a second event into
S&CWADJune: 8 foreign decks (a late-July Win-a-Dual, field 24) were filed under
the June event's name and id, the 8 original June decks' `createdAt` was
restamped from 2026-06-27 to 2026-07-25, the event's size was restated from 37
to 24, and the June norms were recomputed against the wrong field. The gate
promoted it with zero flags, and the corruption reached the deployed graph: a
doubled bracket (two 1sts, two 2nds), two phantom numbered careers (Brennan C 2,
Chifley C 2 via the ADR 0004 same-event split), June finishes inflated, and June
dates moved a month.

Every guard waved it through for a reason each guard's own record had accepted:
the foreign decks carried fresh ids the gate has nothing to compare against, and
every field rewritten on the absorbed decks (`createdAt`, `eventSize`,
`placementNorm`) was classed volatile under ADR 0003, a classification whose
`createdAt` entry the code itself carried as an open question ("whether a
corrected registration date is a fact to accept or a change to review").

## Decision

Two guards, one per half of the merge, both loud by design (an alarm that is a
line in a report is not an alarm):

1. **A registration date is a historical fact.** `createdAt` joins the
   immutable projection in `ingest._deck_hash`. A restamp flags the deck, pins
   it at the date first fetched (ADR 0008's retain-old), and prints a REVIEW
   REQUIRED banner. Derived over every snapshot pair held, the change fires on
   exactly the 8 restamped June decks and nothing else in history.

2. **One Tournament crowns one winner.** `build._check_sole_winner` aborts the
   build (`TwoWinners`, beside `YearStraddle`: live graph untouched) when a
   Tournament's rank-1 group holds more decks than the widest tie band its own
   titles declare (`models.tier_width_from_title`). A merge cannot dodge it:
   every event brings a winner, and even two merged split finals put four decks
   in a band of two. Split finals, "Top N" cohort cuts, Teams events, and
   deep-swiss ties (LPMPerth's two source-scored 25ths) all stay out of scope;
   over the full corpus the guard fires on the merged S&CWADJune alone.

The corruption itself was repaired at the source per the project's standing
practice: the 8 foreign decks were refiled in the 2026-08-15 snapshot under the
event's real name, S&CWADJuly (field 24, per the repo owner), and the 8 June
records restored verbatim from the 2026-07-18 snapshot (field 37, June dates).
No override layer, no curation entry: the snapshot now records what happened.

## Consequences

- The graph holds 112 events; Brennan C and Chifley C are single careers again.
- While upstream remains merged, every future fetch will flag the 16 decks
  (restamped dates on the June 8, a changed event on the July 8) and pin them
  at the corrected values: the graph stays right, and the banner keeps naming
  the problem until the source fixes it. Resolving the flags means fixing
  upstream, not editing the pin.
- A genuinely corrected registration date now also flags and waits for a
  human. History prices that at zero: no other `createdAt` rewrite has ever
  been fetched.
- Bulk test fixtures may no longer park whole cohorts on exclusive "1st"
  titles; they declare the tie band they mean (`tests/test_trends.py`,
  `_shared_win`).
