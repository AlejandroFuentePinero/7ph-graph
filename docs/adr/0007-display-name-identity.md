# Display name is the primary player identity

Extends ADR 0004 and ADR 0005. Those keyed a Pilot node on the upstream `pilot`
id and treated a shared display name under two ids as an unresolved candidate a
human had to confirm. This ADR makes the recovered **display name** the primary
identity: two ids that recover the same name are the same person, joined
automatically on every build.

## Why the id is not the identity

ADR 0005 established that the `pilot` id is a per-name-string pseudonym, not an
account: the same person appears under different ids whenever their name was
recorded differently (a handle like `alejandrofp` instead of `Alejandro D`), and
a registration whose id the source dropped lands in the null bucket under its
recovered name instead. So the id is auto-generated, non-curated, and splits one
person across several keys. The human name recovered from the deck title is the
stable, meaningful identity; the id is just how one registration was tagged.

## The assumption

**Two registrations that recover an identical display name (case-insensitively)
are the same person.** This is an assumption, not a proof: two distinct people
can share a name. We accept it deliberately, because the data carries no signal
that separates same-named people who never met at an event, and deck or
archetype similarity cannot supply one (teammates share lists, ADR 0005). The
one place the data *does* carry that signal is a single event, and that case is
handled below rather than by refusing the join.

## The join

`resolve_pilots` folds all pilots sharing a casefolded display name onto one
canonical id: a real id when the group has one (the busiest, id to break ties),
never a synthetic `nan:` key, so a null-bucket orphan joins into the real person
of that name. Every deck is repointed to the canonical id and each join is
logged in the reconciliation report, so it is never silent. An untitled deck
recovers no name, only the `unknown` placeholder, so it carries no identity and
never joins another.

The join runs **before** the event split (ADR 0004), which preserves the
one-deck-per-pilot-per-event invariant: if joining two same-named ids puts two
decks under one person at one event, the split deals them back into numbered
people (`Tom M 1`, `Tom M 2`) for that event. So same-named players are merged
across disjoint events but kept apart where they actually collided, which is the
only place the data justifies separating them.

## Consequences

The graph now has one node per distinct recovered name (1297 pilot nodes fell to
1257 on the current snapshot, 40 id groups joined). The pins from ADR 0005 still
matter and now also trigger joins: pinning `alejandrofp -> Alejandro D` lets that
id join the native `Alejandro D`. The under-merge report shrinks, because an
exact-name match is no longer a candidate to review; it is resolved. What remains
for the dictionary is only the *non-identical* near-duplicates (typos, an initial
expanded to a full surname, an added middle initial), which stay proposals until
a human confirms them.
