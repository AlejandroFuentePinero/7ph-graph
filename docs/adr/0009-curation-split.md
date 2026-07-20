# Curation split: separating same-name strangers

Extends ADR 0007 and ADR 0005. ADR 0007 makes the recovered display name the
primary identity: two ids that recover the same name are joined automatically on
every build. That is an assumption, not a proof (two people can share a name),
and the curation dictionary (ADR 0005) had no way to override it: merge, reject,
name, and deck_pilot could pull ids together or pin a name, but none could keep
two same-named ids apart. So one Grixis "James L" and one Walks "James L" fused
into a single node with no recourse (issue #35, #9 review finding 2).

## The decision

Add a fifth dictionary entry, `[[split]]`, the inverse of `[[merge]]`: it names
upstream ids that share a display name but are different people, and the
identical-name join keeps them apart.

```toml
[[split]]
ids = ["GrixisJamesId", "WalksJamesId"]
```

Like `[[reject]]`, a split of three or more ids expands to all its pairs (the ids
are mutually distinct), and it keys only on stable upstream ids, so it applies
identically on every rebuild. `reject` was not reused: it suppresses a
near-duplicate *name candidate* from the review list, a different decision from
overriding the *identical-name join*, and keeping them distinct keeps each
decision's audit trail its own.

## Where it applies

The join (`_join_identical_names`) now partitions each same-name group with
union-find: every pair is joined unless a split keeps it apart. Absent any split,
a group folds to one person exactly as before, so a build with no `[[split]]`
entries is unchanged. Each resulting person folds onto its own canonical id by
the existing rule (a real id over a synthetic `nan:` key, busiest to break ties),
and the split is logged in the reconciliation report as a `name_splits` entry, so
the separation is never silent.

## Under-specified splits abort

A split is only meaningful over a whole same-name group. If three ids recover one
name and only one pair is split, the third id joins both and transitively
re-fuses the split pair. Rather than silently re-fusing (the exact trust hole
this ticket closes), the build raises `CurationError` and asks the maintainer to
split the third id from one side too. This mirrors ADR 0005's rule that a
self-contradicting dictionary aborts the build.

## What it does not do

Split keys on real upstream ids, which is the concrete case #35 raised (two real
ids fused by the join). Peeling a single deck off a shared pseudonym is already
possible by pointing its `[[deck_pilot]]` at its own id, so a null-bucket orphan
that should not join a real person of that name is handled there, before the vote
runs. Split is never automatic: only a maintainer's dictionary entry separates
ids, so a real person who plays different decks at different events stays one
node.
