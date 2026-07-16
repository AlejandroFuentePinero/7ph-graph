# Pilot curation: a checked-in dictionary of human decisions

Extends ADR 0004. That ADR resolves what the data decides on its own and emits a reconciliation report; nothing consumed the report, so every rebuild re-flagged the same cases. This ADR adds a curated-overrides source the build reads and applies deterministically, so a maintainer's judgement survives every rebuild and the report narrows to only the still-undecided cases (issue #9).

## What the upstream id actually is

The `pilot` id is not an account number. 1221 of 1248 ids are drawn from a closed 22x20x20 vocabulary of `<Adjective><Colour><Animal>` names (`LunarRedFalcon`), and 90 carry a 3-hex suffix (`ShadowMaroonWolf1A3`). That suffix count matches the birthday-paradox prediction (80.9 collisions expected, 90 observed), and all 82 collided bases hold different display names: the id is a random per-registration pseudonym of a name string, and the generator broke its own clashes with the suffix. So the id is stable but neither one-account nor one-person: two people who share a name can hold different ids, and the same name can recur under many.

## The curation dictionary

`curation/pilots.toml` holds four kinds of decision, each keyed only on a stable upstream identifier (a pilot id or a deck id), never on a derived value like a display name or a synthetic key (`nan:darcy`, `LunarRedFalcon#2`) that shifts when data changes. That is what makes a decision timeless: recorded once, it applies identically to every future ingestion.

- **merge**: collapse several ids onto one canonical id, flattened transitively (union-find), so `Alexadner J -> Alexander J` and `Alex J -> Alexander J` land on one node.
- **reject**: mark two ids as different people, suppressing the pair from the candidate report for good.
- **name**: pin a display name over the majority vote (`alejandrofp -> Alejandro D`).
- **deck_pilot**: reassign one deck to a real pilot id, resolving a null-pilot deck to its owner before the name vote.

An absent file is not an error: the heuristics alone still build a graph. A malformed file, or one that contradicts itself (a merged group naming two canonical ids), aborts the build.

## Wave 1: only hard facts merge

The heuristics find and rank candidates but never merge two ids on their own. A recovered name is classified by how two names relate: `exact`, `first-name` (a bare first name against `First I`), `handle` (`OdeB` against `Oden B`), `nickname` (`Chris`/`Christopher`), and `typo` (one Damerau edit, so `Alexadner`/`Alexander` but not `Jake`/`Jack`). None of these is a hard fact of identity: two different people are reasonably named `Connor P`, a bare `Noelle` may be a different Noelle, and a handle is lossy (`JonB48` fits any `Jon* B`). So in wave 1 every name-similarity match is a proposal a human confirms into the dictionary; only the dictionary merges ids. Deck or archetype similarity is not a tie-breaker either, because teammates share lists, so decklist overlap cannot prove identity; it is shown in the review report as evidence only.

## Duplicate registrations

Two decks that share an upstream id, an event, a recovered name, and a card-for-card identical list are one registration entered twice (teammates share a list but not a name). The build keeps the best placement, drops the rest, and logs each drop in the report, so the deletion is never silent. This is the one deletion wave 1 makes automatically, because exact-identical-under-one-name is a hard fact. Near-identical lists are left as report candidates, not deleted.

## Title parsing corrected

Name recovery previously misread several title shapes. A leading `*` or a `Top N` cut is a placement, not a name; a name that is only a points marker (`8pt Blue Moon`) means the pilot field was empty; and a separator-less title (`1st Ben N Lurrus Breach PoGTeams2024`) is split by subtracting the source's own deck name and event from the tail. A placement the source left null is also recovered from the title, taking a range's worst rank (`5th-8th` and `Top 8` both read as 8), while the source value always wins when present.

## Wave 2/3: adjudication heuristics

Wave 1 hands every name match to a human. These patterns let a maintainer decide in bulk; each still records its verdict as a dictionary entry keyed on upstream ids. "Overlap" is the maximum card-set Jaccard across the two ids' decks (7PH decks are singleton, so a deck is a set).

Same person:

- **Single-deck slip.** When the rarer side has exactly one deck, it is a one-time data-entry slip at a single event. If that lone deck shares an archetype with the matching name and overlaps at least ~0.5 on disjoint events, it is the same person. Confirmed by hand down to 0.63; the overlap floor is the safety, excluding low-overlap namesakes.

Different people:

- **Shared-event separator.** Two distinct ids that both register at two or more events are two humans: one registration per person per event, and at each shared event they field different decks. A single shared event is not enough on its own, because one person can play two flights under two spellings. But a single shared event with different decks *does* separate when the overlap driving a proposed merge comes from a netdecked archetype at *other* events and the name link is weak (a handle or a bare-initial surname stub): that overlap is community list-sharing, not identity. Do not merge across it in the hope that the event split will re-separate the ids by placement (ADR 0004) later; the split keys on one id, so once merged the two people are fused before it runs. An independent blind re-derivation (issue #9 audit) caught several such over-merges that forward candidate review had accepted as corroborated.
- **Both-established separator.** Both sides carrying four or more decks yet overlapping below 0.5 are two independent careers. A real alias shares a deck signature, and a one-time typo does not accumulate its own multi-deck career.

Traps that defeat the name shape:

- **Handle collision.** A handle is first-three-letters + surname initial, which collides across people (`CalT` = Caleb / Calum / Callum, `DanT` = Dan / Daniel / Danny). It is never structurally certain and must earn a merge by deck overlap like any other shape. Treating handles as structural once chained three distinct people at ~0.05 overlap.
- **False-friend names.** An edit-distance-1 match can be two different given names, not a typo: Andrea / Andrew, Antonia / Antonio, Jose / Rose, Bill / Will (distinct William nicknames). The `typo` shape cannot tell these apart, and high deck overlap does not rescue them, because teammates net-deck identical lists (ADR 0005): a 1.00 hit at non-shared events, with different decks at the shared ones, is list-sharing, not one person.

  The **edit type** is what splits the typo bucket for batching. A single insertion, deletion, transposition, or diacritic/punctuation change scrambles a name into a *non-name* variant of itself and cannot reach a different real name in one step (`Niocholas`, `Geroge`, `Trisitan`); these are same-person, subject to the corroboration caveat below. A single **substitution** is the danger zone: it flips gender or identity at any length (Tim / Tom, Antonia / Antonio, Dan / Ian) and needs a human. And when the substituted letter is the *surname initial* rather than the first name, it is simply a different surname and a different person (Matt A / Matt B, Charlie B / Charlie M). Short insert/delete false-friends exist too (Joe / Jose, JD / Jed), so restrict the insert/delete auto-merge to names of five or more letters.

When a merge does land, the canonical id is chosen for the best display name, not the largest deck count: prefer the correct spelling and the real full name over a busier misspelling or a handle (`Gerard N` over the busier `Gerad N`; `Jinghao Y`, `Chia Wei L`). Because the merged node's display name is a per-deck majority vote, a merge onto a low-deck real name may need a `name` pin to stop a busier handle winning the vote (`AarW15` back to `Aaron W`).

A **name-only merge needs deck corroboration.** "The variant is a non-name, so it must be this one real bearer" is sound only when a deck overlaps to back it; with the decks actively disjoint (`Geroge H` at 0.04, `Axexander G` at 0.10) it is an unfalsifiable name bet, and those are held, not merged. The safe auto-merge bar is a same-name shape with overlap at least 0.5 on a shared archetype at *disjoint* events; a shared event is the teammate/event-split signal and is held.

## Consequences

The reconciliation report now distinguishes uncurated candidates from already-decided ones and lists dropped duplicates, so the human-review list shrinks as decisions accrue. The append-stable threading of a split id's decks into careers (replacing ADR 0004's placement-rank dealing) is deferred to a second wave.
