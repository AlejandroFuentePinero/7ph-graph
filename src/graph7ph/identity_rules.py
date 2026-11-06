"""The rules that decide the clear identity cases at build (issue #273, ADR 0028).

Every build derives one of three states for every candidate pair the name
heuristics raise (:func:`pilots.name_relation`), and writes nothing:

- **merged** by rule: the two ids' decks say one person, on a bar set per name
  shape and judged against a matched stranger pool;
- **separated**: the two ids both registered at one event, the one structural
  fact the data carries (a deck is one pilot's single entry at one event, ADR
  0005), so they are two people;
- **watching**: everything else, which is two nodes, the graph's default.

A rule merge is recomputed every build, so it comes apart on its own when it
stops clearing its bar, and it is immune to upstream reusing an id (issue #261):
it reads whoever owns the id now. The dictionary keeps the overrides: a
``[[reject]]`` or ``[[split]]`` beats a rule merge and a ``[[merge]]`` beats the
separator, both by pipeline order (the rules read the resolved node after the
dictionary and the identical-name join have been applied), and every pair a rule
fires on that the dictionary overrode is listed so the override stays visible.
"""

from collections import Counter
from dataclasses import dataclass, replace
from itertools import combinations

from graph7ph.curation import Curation
from graph7ph.pilots import (
    _FIRST_INITIAL, _HANDLE, _candidate_pairs, _card_set, _jaccard, name_relation,
    recovered_names,
)

# The deck bar per name shape, raw card-set overlap (Jaccard over the whole 75)
# between the two ids' closest decks on one primary archetype at different events.
# A choice under asymmetric loss (a false merge fuses two people for every metric
# downstream, a pair left apart costs nothing), measured on the dictionary's
# labelled pairs rather than derived from them: only the substitution shape
# carries rejects, so the data bounds no other shape's bar from below (ADR 0028).
#
# "typo" is a non-name typo, which cannot reach another real name in one slip:
# an insertion or deletion on a first name of five or more letters, or a
# transposition of any length. It takes the lowered bar, which keeps the rule
# that a name-only bet needs a deck behind it (ADR 0005). A handle, a nickname
# and a bare first name each fit several people, so they take the high bar, or
# the top percentile of the matched stranger pool below. A substitution typo and
# a short insertion or deletion ("look-alike": Andrea / Andrew, Joe / Jose) are
# as often two people as one and never merge by rule.
TYPO_BAR = 0.4
NAME_BAR = 0.9
# The shortest first name an insertion or deletion is trusted on (ADR 0005).
TYPO_LETTERS = 5

# The matched stranger pool a raw overlap is judged against: deck pairs of
# distinct pilots on the same primary archetype and colour, registered a
# comparable gap apart (within a factor of two of the pair's own gap, with a
# 30-day floor so same-week pairs are not matched against nothing). Exhaustive,
# never sampled, so a pair's percentile cannot depend on the order pairs are
# scored. A pool under the floor yields no percentile at all: 1% of 40 pairs is
# no pair, and "top 1%" of such a pool would be whichever pair happened to score
# highest.
POOL_FLOOR = 100
GAP_FLOOR_DAYS = 30
TOP_ONE_IN = 100  # "at or above the 99th percentile": at most one pair in a hundred scores as high

# The stranger base rate the reconciliation report carries every build, the
# out-of-sample instrument the ADR records beside the pool definition: the share
# of every matched stranger pair (all gaps) scoring at or above each bar.
BASE_RATE_BARS = (TYPO_BAR, 0.5, 0.8, NAME_BAR, 1.0)

# Why a side may hold several people (the hub guard).
BARE_NAME = "bare first name"
NUMBERED_FAMILY = "family of several entries at one event"
TWO_PEOPLE = "match for two ids kept apart"
NO_LIST: frozenset = frozenset()  # a deck whose list the snapshot does not carry


@dataclass(frozen=True)
class RuledPair:
    """One candidate pair and the state the rules derived for it this build.

    ``pilots`` are the two node ids, sorted, and ``names`` their display names
    in the same order. ``shape`` is the name shape the bar was set for: the
    relation :func:`pilots.name_relation` found, the typo relation split by edit
    type (``typo``, ``look-alike``, ``handle``, ``nickname``, ``first-name``). ``overlap`` is the
    best card-set overlap between the two ids' decks on one primary archetype
    at different events, ``archetype`` that archetype, and ``pool`` and
    ``at_or_above`` the matched stranger pool it was judged in and how many of
    the pool scored at least as high (both ``None`` where no pool reached the
    floor). ``shared_events`` are the events both registered at and ``hubs``
    which of the two is a hub and why. ``canonical`` is the id the merged node
    keeps, set only on a merged pair. ``overridden_by`` names the dictionary
    kind that overrode the rule, set only on the override list, and
    ``kept_apart`` the pair that ruling is on where it reached this pair
    through a chain (issue #288).
    """

    pilots: list[str]
    names: list[str]
    shape: str
    state: str  # "merged" | "separated" | "watching"
    overlap: float
    archetype: str | None
    pool: int | None
    at_or_above: int | None
    shared_events: list[str]
    hubs: dict[str, str]
    canonical: str | None = None
    overridden_by: str | None = None
    kept_apart: list[str] | None = None


@dataclass(frozen=True)
class RuleVerdicts:
    """What one build's rules derived: the ruled pairs, the overrides, and the pool's base rate."""

    pairs: list[RuledPair]  # every candidate pair the dictionary leaves to the rules
    overrides: list[RuledPair]  # pairs a rule fired on that the dictionary overrode
    rejected: int  # candidate pairs a [[reject]] or [[split]] keeps apart
    stranger_base_rate: dict[str, float]  # bar -> share of matched stranger pairs at or above it


def rule_states(
    pilots, decks, deck_pilot: dict[str, str], decklists: dict[str, object] | None,
    curation: Curation,
) -> RuleVerdicts:
    """The three states, over the real nodes as the dictionary and the join left them.

    ``pilots`` are the resolved real pilots after the identical-name join and
    ``deck_pilot`` the deck-to-node map as the join left it, before the event
    split: a rule merge folds there, so a collision the merge creates is
    renumbered by the split, and the hub guard reads a same-event collision off
    the node's own decks rather than off the split's output.
    """
    names = {p.pilot: p.display_name for p in pilots}
    cards = _card_sets(decklists)
    decks_of: dict[str, list] = {pid: [] for pid in names}
    raw_ids: dict[str, set[str]] = {pid: {pid} for pid in names}
    for deck in sorted(decks, key=lambda d: d.deck_id):
        node = deck_pilot[deck.deck_id]
        if node in decks_of:
            decks_of[node].append(deck)
            raw_ids[node].add(deck.pilot)
    strangers = _Strangers(decks_of, cards)

    def kept_apart_by(a: str, b: str) -> str | None:
        # A reject or split is recorded on raw ids, and the join or a merge may
        # have folded one into its node: the ruling travels with the raw id.
        for kind, is_kept in (("reject", curation.is_rejected), ("split", curation.is_split)):
            if any(is_kept(x, y) for x in raw_ids[a] for y in raw_ids[b]):
                return kind
        return None

    pairs: list[RuledPair] = []
    overrides: list[RuledPair] = []
    rejected = 0
    for a, b in sorted(tuple(sorted(pair)) for pair in _candidate_pairs(names)):
        relation = name_relation(names[a], names[b])
        if not relation:
            continue
        kept_apart = kept_apart_by(a, b)
        ruled = _judge(a, b, names, relation, decks_of, cards, strangers)
        if kept_apart is None:
            pairs.append(ruled)
        else:
            rejected += 1
            if ruled.state == "merged":
                overrides.append(_overridden(ruled, kept_apart))
    pairs, chained = _guard_chains(pairs, kept_apart_by)
    overrides += chained + _separator_overrides(decks, curation, cards, strangers)
    return RuleVerdicts(pairs, overrides, rejected, strangers.base_rate())


def _judge(a: str, b: str, names, relation: str, decks_of, cards, strangers) -> RuledPair:
    """One pair's state, from the composed rule: bar, shared-event veto, hub guard."""
    shape = _shape(names[a], names[b], relation)
    overlap, archetype, pair = _best_overlap(decks_of[a], decks_of[b], cards)
    pool = at_or_above = None
    if pair is not None and shape in ("handle", "nickname", "first-name") and overlap < NAME_BAR:
        busier = max((a, b), key=lambda pid: (len(decks_of[pid]), pid == a))
        x, y = pair if busier == a else (pair[1], pair[0])
        measured = strangers.percentile(archetype, x.colour, _gap_days(x, y), overlap)
        if measured is not None:
            pool, at_or_above = measured
    clears = _clears(shape, overlap, pool, at_or_above)
    events = {pid: _events(decks_of[pid]) for pid in (a, b)}
    shared = sorted(set(events[a]) & set(events[b]))
    hubs = {
        pid: why for pid in (a, b)
        if (why := _hub(names[pid], relation, events[pid])) is not None
    }
    # The shared event vetoes a merge for every pair, hub or not, and separates
    # only a pair with no hub side: a bare first name or a numbered family can
    # hold several people, so sharing an event with one of them proves nothing.
    # A bare first name merges only when its side is a single deck, for the same
    # reason: a second deck under it may be a second person.
    if shared:
        state = "watching" if hubs else "separated"
    elif clears and all(len(decks_of[pid]) == 1 for pid, why in hubs.items()
                        if why == BARE_NAME):
        state = "merged"
    else:
        state = "watching"
    canonical = (_canonical((a, b), names, {pid: len(decks_of[pid]) for pid in (a, b)})
                 if state == "merged" else None)
    return RuledPair(
        pilots=[a, b], names=[names[a], names[b]], shape=shape,
        state=state, overlap=round(overlap, 3), archetype=archetype, pool=pool,
        at_or_above=at_or_above, shared_events=shared, hubs=hubs, canonical=canonical,
    )


def _guard_chains(pairs: list[RuledPair], kept_apart_by) -> tuple[list[RuledPair], list[RuledPair]]:
    """Merged pairs demoted to watching where their chain holds a pair kept apart.

    Rule merges sharing an id fold transitively (:func:`fold_merges`), so an id
    that clears the bar against two ids kept apart, by the rule's own separator
    or by a dictionary ``reject`` or ``split`` (issue #288), would fuse the two
    through it. No merge in such a chain folds: each watches, with the id
    outside the kept-apart pair marked as the side matching two people. The
    dictionary-apart pair never reaches ``pairs``, so it is read off the ids
    under each chain with ``kept_apart_by``; the merges it held back are also
    returned, marked with its kind, for the override list.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in pairs:
        if pair.state == "merged":
            parent[find(pair.pilots[0])] = find(pair.pilots[1])
    chains: dict[str, list[str]] = {}  # chain root -> its ids
    for pid in parent:
        chains.setdefault(find(pid), []).append(pid)
    apart: dict[str, set[str]] = {}  # chain root -> ids of its kept-apart pairs
    # chain root -> the first dictionary ruling found on it, as (kind, pair)
    ruling: dict[str, tuple[str, list[str]]] = {}
    for pair in pairs:
        a, b = pair.pilots
        if pair.state == "separated" and a in parent and b in parent and find(a) == find(b):
            apart.setdefault(find(a), set()).update((a, b))
    for root, ids in chains.items():
        for a, b in combinations(sorted(ids), 2):
            if (kind := kept_apart_by(a, b)) is not None:
                apart.setdefault(root, set()).update((a, b))
                ruling.setdefault(root, (kind, [a, b]))
    guarded: list[RuledPair] = []
    overridden: list[RuledPair] = []
    for pair in pairs:
        if pair.state != "merged" or find(pair.pilots[0]) not in apart:
            guarded.append(pair)
            continue
        root = find(pair.pilots[0])
        hubs = {**pair.hubs, **{pid: TWO_PEOPLE for pid in pair.pilots if pid not in apart[root]}}
        guarded.append(replace(pair, state="watching", canonical=None, hubs=hubs))
        if root in ruling:
            kind, on = ruling[root]
            overridden.append(_overridden(replace(pair, hubs=hubs, kept_apart=on), kind))
    return guarded, overridden


def _hub(name: str, relation: str, events: dict[str, list]) -> str | None:
    """Why this side may hold several people, or ``None``."""
    if relation == "first-name" and not _FIRST_INITIAL.match(name):
        return BARE_NAME
    if any(len(at_event) > 1 for at_event in events.values()):
        return NUMBERED_FAMILY
    return None


def _shape(name_a: str, name_b: str, relation: str) -> str:
    """The name shape a bar is set for; the typo relation splits by edit type."""
    if relation != "typo":
        return relation
    first_a, first_b = (n.rsplit(None, 1)[0].casefold() for n in (name_a, name_b))
    if len(first_a) != len(first_b):
        return "typo" if min(len(first_a), len(first_b)) >= TYPO_LETTERS else "look-alike"
    diff = [i for i in range(len(first_a)) if first_a[i] != first_b[i]]
    if len(diff) == 2 and first_a[diff[0]] == first_b[diff[1]] and first_a[diff[1]] == first_b[diff[0]]:
        return "typo"
    return "look-alike"


def _clears(shape: str, overlap: float, pool: int | None, at_or_above: int | None) -> bool:
    """Whether the deck evidence clears the bar set for this name shape."""
    if shape == "typo":
        return overlap >= TYPO_BAR
    if shape in ("handle", "nickname", "first-name"):
        return overlap >= NAME_BAR or (pool is not None and at_or_above * TOP_ONE_IN <= pool)
    return False


def _canonical(ids, names, deck_count) -> str:
    """The id a merged node keeps: the real name over a handle, then the busiest, then the id.

    The join copies the canonical's display name without a re-vote, so a handle
    must never win it (the Toby M / TobM7 node is named Toby M). A typo is a slip
    that does not accumulate decks (ADR 0005's single-deck slip), so the busier
    spelling is read as the real one; between two real names the busiest wins
    as in the identical-name join. ``deck_count`` is each id's decks on the node.
    """
    return max(ids, key=lambda pid: (not _HANDLE.match(names[pid]), deck_count[pid], pid))


def _best_overlap(decks_a, decks_b, cards) -> tuple[float, str | None, tuple | None]:
    """The closest deck pair on one primary archetype at different events."""
    best, archetype, pair = 0.0, None, None
    for x in decks_a:
        for y in decks_b:
            if x.primary_tag != y.primary_tag or x.event == y.event:
                continue
            overlap = _jaccard(cards.get(x.deck_id, NO_LIST), cards.get(y.deck_id, NO_LIST))
            if overlap > best:
                best, archetype, pair = overlap, x.primary_tag, (x, y)
    return best, archetype, pair


def _events(decks) -> dict[str, list]:
    """Each event a side registered at and its decks there; the ``nan`` sentinel is a hole, not an event."""
    events: dict[str, list] = {}
    for deck in decks:
        if deck.event != "nan":
            events.setdefault(deck.event, []).append(deck)
    return events


def _gap_days(x, y) -> int:
    """Days between two decks' registrations."""
    return abs((x.created_at - y.created_at).days)


def _card_sets(decklists) -> dict[str, frozenset]:
    """Each deck's whole 75 as one set; a deck with no list scores 0 against everything."""
    return {deck_id: _card_set(signature) for deck_id, signature in (decklists or {}).items()}


class _Strangers:
    """The matched stranger pools, each archetype-and-colour group scored once.

    Every pair of decks of distinct pilots in a group is scored exhaustively the
    first time the group is asked for, and a query filters that by gap. No
    sampling, so the answer for a pair is the same whatever was asked before it.
    """

    def __init__(self, decks_of: dict[str, list], cards: dict[str, frozenset]):
        self._groups: dict[tuple[str, str], list] = {}
        for node, decks in decks_of.items():
            for deck in decks:
                self._groups.setdefault((deck.primary_tag, deck.colour), []).append((node, deck))
        self._cards = cards
        self._scored: dict[tuple[str, str], list[tuple[int, float]]] = {}

    def percentile(self, archetype: str, colour: str, gap_days: int, overlap: float) -> tuple[int, int] | None:
        """The pool size and how many of it score at or above ``overlap``, or ``None`` under the floor."""
        lo, hi = max(GAP_FLOOR_DAYS, gap_days) / 2, max(GAP_FLOOR_DAYS, gap_days) * 2
        pool = [score for gap, score in self._scores(archetype, colour) if lo <= gap <= hi]
        if len(pool) < POOL_FLOOR:
            return None
        return len(pool), sum(1 for score in pool if score >= overlap)

    def base_rate(self) -> dict[str, float]:
        """The share of every matched stranger pair, all gaps, at or above each bar.

        The out-of-sample instrument (ADR 0028): what two strangers on one
        archetype and colour score, so a bar can be read against the population
        it sits in. Written into the report every build, so it is re-measured on
        every ingest rather than quoted from the page that first recorded it.
        """
        scores = [score for key in sorted(self._groups) for _, score in self._scores(*key)]
        return {str(bar): (sum(1 for s in scores if s >= bar) / len(scores) if scores else 0.0)
                for bar in BASE_RATE_BARS}

    def _scores(self, archetype: str, colour: str) -> list[tuple[int, float]]:
        key = (archetype, colour)
        if key not in self._scored:
            self._scored[key] = [
                (_gap_days(x, y), _jaccard(self._cards.get(x.deck_id, NO_LIST), self._cards.get(y.deck_id, NO_LIST)))
                for (node_x, x), (node_y, y) in combinations(self._groups.get(key, []), 2)
                if node_x != node_y
            ]
        return self._scored[key]


def _overridden(ruled: RuledPair, by: str) -> RuledPair:
    """The same pair, marked with the dictionary kind that overrode the rule."""
    return replace(ruled, overridden_by=by)


def _separator_overrides(decks, curation, cards, strangers) -> list[RuledPair]:
    """Dictionary-merged pairs the separator would have parted, read at the raw ids.

    A ``[[merge]]`` folds its ids before the rules run, so the shared event
    between two of its members is invisible at the node. It is read here off
    each raw id's own decks, under the same hub guard, so a human ruling that
    crosses the structural fact stays listed every build rather than being
    found only by whoever next reads the file.
    """
    groups: dict[str, list[str]] = {}
    for member, canonical in curation.merges.items():
        groups.setdefault(canonical, [canonical]).append(member)
    if not groups:
        return []
    names = recovered_names(decks)
    decks_of: dict[str, list] = {}
    for deck in sorted(decks, key=lambda d: d.deck_id):
        decks_of.setdefault(deck.pilot, []).append(deck)
    found: list[RuledPair] = []
    for members in groups.values():
        live = sorted(pid for pid in set(members) if pid in decks_of)
        for a, b in combinations(live, 2):
            relation = name_relation(names[a], names[b])
            if not relation:
                continue
            ruled = _judge(a, b, names, relation, decks_of, cards, strangers)
            if ruled.state == "separated":
                found.append(_overridden(ruled, "merge"))
    return found


def fold_merges(pairs: list[RuledPair], names: dict[str, str], deck_count: Counter) -> dict[str, str]:
    """Each merged id onto the id its component keeps, transitively.

    Two rule merges sharing an id are one person three ways (every such case on
    file is three spellings of one name inside one dictionary merge), so merged
    pairs are unioned and each component keeps the id :func:`_canonical` picks
    over the whole component. A chain through a pair the rule separated never
    reaches here: :func:`_guard_chains` demotes its merges to watching.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in pairs:
        if pair.state == "merged":
            a, b = pair.pilots
            parent[find(a)] = find(b)
    components: dict[str, list[str]] = {}
    for pid in parent:
        components.setdefault(find(pid), []).append(pid)
    folded: dict[str, str] = {}
    for members in components.values():
        keep = _canonical(members, names, deck_count)
        for pid in members:
            if pid != keep:
                folded[pid] = keep
    return folded
