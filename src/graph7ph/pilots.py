"""Recover readable, deduplicated pilots from the raw deck data (ADR 0004).

The upstream ``pilot`` field is a stable id (often a pseudonym), so it is the
node key; the human name lives only in the deck title. This module recovers a
Display Name per pilot from those titles by majority vote with fuzzy
consolidation of spelling variants, re-keys the null-pilot decks as
low-confidence per-name pilots, and emits a reconciliation report of the cases
the data cannot resolve on its own.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from graph7ph.curation import Curation, CurationError, DeadEntry, dead_entries
from graph7ph.models import PLACEMENT_TOKEN

# A deck title reads "<placement> <name> - <deck> - <event>". The name is what
# is left after dropping the leading placement token and taking the segment
# before the deck separator. The token's grammar belongs to the source parse
# (models.PLACEMENT_TOKEN), which reads a rank out of it; here it is only
# stripped, so that a rank is never mistaken for a name.

# The name/deck separator: a hyphen or en/em dash with whitespace on at least
# one side (" - ", "- ", " –"). Requiring a space keeps an intra-name hyphen
# ("John-Paul", "Chris K-H"), which has no surrounding spaces, from splitting.
_SEPARATOR = re.compile(r"\s+[-–—]\s*|\s*[-–—]\s+")

# A deck's points marker ("8pt Blue Moon", "7pt Storm"). It opens a deck name,
# never a person's, so a "name" starting with one is really the deck: the title
# left the pilot field empty ("121st  - 8pt Blue Moon - HighlanderWorlds26").
_POINTS_MARKER = re.compile(r"^[78]\s*pts?\b", re.IGNORECASE)


def display_name_from_title(
    title: str | None, *, deck_name: str | None = None, event: str | None = None
) -> str | None:
    """Recover the pilot's display name from a deck title, or ``None``.

    Strips a leading placement token and takes the segment before the deck
    separator. Some titles carry no separator at all ("1st Ben N Lurrus Breach
    PoGTeams2024"), so the source's own ``deck_name`` and ``event`` are
    subtracted from the tail when given, leaving just the name. Best-effort:
    residual noise is expected to lose the majority vote in
    :func:`resolve_pilots`.
    """
    if title is None:
        return None
    stripped = PLACEMENT_TOKEN.sub("", title, count=1)
    # A placement token is sometimes followed by its own separator
    # ("05th-8th - Kyle G - ..."), so take the first non-empty segment.
    segments = _SEPARATOR.split(stripped)
    # The deck and event are subtracted only on the separator-less fallback
    # ("1st Ben N Lurrus Breach PoGTeams2024"), where the name is not otherwise
    # isolated. When a separator already delimits the name segment, subtracting
    # them would clip a real surname that equals the deck ("John Storm" ->
    # "John") and false-join it to any other first name (F11).
    isolated = len(segments) > 1
    for segment in segments:
        name = segment.strip()
        if not name:
            continue
        if not isolated:
            name = _drop_suffix(_drop_suffix(name, event), deck_name)
        return None if _POINTS_MARKER.match(name) else name
    return None


def _display_name(deck) -> str | None:
    """The display name recovered from a deck, using its own deck name and event."""
    return display_name_from_title(
        deck.name, deck_name=deck.deck_name, event=deck.event
    )


def _drop_suffix(name: str, suffix: str | None) -> str:
    """Drop a trailing ``suffix`` from ``name``, unless nothing would survive.

    The guard matters where the source's own ``deck_name`` is the person's name
    ("5th-8th - Liam B - Pats Birthday Brawl" parses upstream to deck "Liam B"),
    which would otherwise subtract the very name we are recovering.
    """
    if not suffix or not name.casefold().endswith(suffix.casefold()):
        return name
    return name[: -len(suffix)].strip(" -–—") or name


# Pilots whose upstream id is a null placeholder rather than a real identity.
# "nan" is a pandas/numpy stringification artifact from an untrusted upstream
# serializer (ADR 0003), so it is not the only shape a lost id takes: '', 'none',
# 'null', 'n/a' all mean "no pilot" too. Matched case-insensitively so a
# serializer change cannot silently collapse pilotless decks into one node (F8).
NULL_PILOT_IDS = frozenset({"", "nan", "none", "null", "n/a"})


def _is_null_pilot(pilot_id: str) -> bool:
    """Whether an upstream id is a null placeholder, not a real identity."""
    return pilot_id.casefold() in NULL_PILOT_IDS

# Recovered names are shaped "<first...> <surname initial>". Two are treated as
# spelling variants of one person only above these similarities. Deliberately
# conservative: better to leave two apart for human review than to merge
# distinct people (e.g. "Jordan C" and "Jordan B" must stay separate).
_FUZZY_THRESHOLD = 0.8
_FIRST_NAME_THRESHOLD = 0.7


@dataclass(frozen=True)
class ResolvedPilot:
    """A Pilot node: the upstream id key, its recovered name, and confidence."""

    pilot: str
    display_name: str
    low_confidence: bool


@dataclass(frozen=True)
class VariantCluster:
    """Spelling variants that consolidated into one pilot's display name."""

    pilot: str
    display_name: str
    variants: dict[str, int]  # each merged spelling -> how many decks used it


@dataclass(frozen=True)
class UnderMerge:
    """Two pilot ids whose names say they may be one person split in two.

    A candidate only, never acted on by the heuristics: the build merges two ids
    only when a human has recorded the decision in the dictionary (issue #9).
    ``relation`` is how the names relate (see :func:`name_relation`), which is
    how much the shape can be trusted and how the review list is ranked.
    """

    display_name: str
    pilots: list[str]
    relation: str


@dataclass(frozen=True)
class DroppedDuplicate:
    """A deck removed as a duplicate registration, kept for the record.

    Same upstream id, same event, same recovered name, and a card-for-card
    identical list as ``kept``: one registration entered twice, not two people
    (teammates share a list but not a name). Logged, never silently dropped.
    """

    dropped_deck: str
    kept_deck: str
    pilot: str
    event: str
    display_name: str


@dataclass(frozen=True)
class JoinedName:
    """Several ids that recovered one display name, joined into one person.

    Display name is the primary player identity (ADR 0007): ids that resolve to
    the same name are the same person, whether all are real registrations or one
    is a null-bucket orphan whose upstream id the source lost. Logged, so the
    join is never silent."""

    display_name: str
    canonical: str  # the id the joined pilot keeps
    merged: list[str]  # every id folded in (canonical included)


@dataclass(frozen=True)
class SplitName:
    """One display name a curated decision kept apart into several people (#35).

    The identical-name join (ADR 0007) folds every id sharing a display name into
    one person; a ``[[split]]`` (issue #35) or a ``[[reject]]`` (issue #74) entry
    declares that two of those ids are strangers who share a name (one Grixis
    "James L", one Walks "James L"). This records the override: the ``people`` are
    the canonical ids the name group was split into, so the separation is never
    silent."""

    display_name: str
    people: list[str]


@dataclass(frozen=True)
class EventSplit:
    """One pilot id that entered an event more than once, split into people.

    A deck is one pilot's single entry at one event, so the duplicates were
    distinct people sharing a name; ``people`` are the numbered identities they
    were split into (ADR 0004)."""

    display_name: str
    people: list[str]


@dataclass(frozen=True)
class MultiNameId:
    """One upstream id whose decks recovered more than one surname family.

    A single id is one identity, so decks that recover two different surnames
    (e.g. "Tom H" and "Tom M") are likely two people reusing one id. The majority
    name wins the node and the minority would otherwise vanish, so the id is
    surfaced here for a human (ADR 0007, issue #39)."""

    pilot: str
    display_name: str  # the majority name that won the node
    names: list[str]  # every distinct recovered name, the minorities included


@dataclass(frozen=True)
class Reconciliation:
    variant_clusters: list[VariantCluster]
    under_merges: list[UnderMerge]  # UNCURATED candidates only; curated ones drop off
    null_pilots: list[ResolvedPilot]  # the re-keyed null bucket (ADR 0004)
    event_splits: list[EventSplit]  # ids split for same-event collisions (ADR 0004)
    dropped_duplicates: list[DroppedDuplicate]  # removed duplicate registrations
    joined_names: list[JoinedName]  # ids collapsed for sharing a display name
    curated: int  # decided pairs off the review list: rejections plus applied merges
    dead_entries: list[DeadEntry] = field(default_factory=list)  # entries matching no id (issue #37)
    multi_name_ids: list[MultiNameId] = field(default_factory=list)  # ids spanning >1 surname (issue #39)
    name_splits: list[SplitName] = field(default_factory=list)  # same-name ids kept apart by a split or reject (issues #35, #74)


@dataclass(frozen=True)
class PilotResolution:
    deck_pilot: dict[str, str]  # deckId -> resolved pilot key
    pilots: list[ResolvedPilot]
    report: Reconciliation
    dropped_decks: frozenset[str]  # deck ids removed as duplicates; the build skips them


def resolve_pilots(
    decks,
    curation: "Curation | None" = None,
    decklists: dict[str, object] | None = None,
) -> PilotResolution:
    """Resolve decks to keyed, named pilots and a reconciliation report.

    Real pilots keep their upstream id and take a majority display name (with
    fuzzy-consolidated spelling variants). Null-pilot decks are re-keyed as
    low-confidence per-name pilots. The heuristics never merge two ids on their
    own; only the ``curation`` dictionary does that (issue #9), applied here as:
    a deck reassigned to a real pilot, ids collapsed onto a canonical id, and a
    pinned display name. When ``decklists`` (deckId -> a hashable card signature)
    is given, card-for-card duplicate registrations are dropped and logged.

    The report surfaces what still needs a human: variant clusters that were
    merged, the re-keyed null bucket, and the uncurated under-merge candidates.
    """
    curation = curation or Curation.empty()

    # A deck's pilot is its upstream id, unless the dictionary reassigns the deck
    # (a null-pilot deck to its real owner) or merges the id onto a canonical one.
    def resolved_id(deck) -> str:
        return curation.canonical(curation.deck_pilots.get(deck.deck_id, deck.pilot))

    dropped = _drop_duplicates(decks, decklists, resolved_id) if decklists else []
    gone = {d.dropped_deck for d in dropped}
    decks = [d for d in decks if d.deck_id not in gone]

    deck_pilot: dict[str, str] = {}
    real_pilots: list[ResolvedPilot] = []
    null_pilots: list[ResolvedPilot] = []
    variant_clusters: list[VariantCluster] = []
    multi_name_ids: list[MultiNameId] = []

    real: dict[str, list] = {}
    null: dict[str, list] = {}
    for deck in decks:
        pid = resolved_id(deck)
        bucket = null if _is_null_pilot(pid) else real
        bucket.setdefault(pid, []).append(deck)

    for pilot_id, group in real.items():
        names = [_display_name(d) for d in group]
        display, merged = _choose_display_name(names, fallback=pilot_id)
        display = curation.names.get(pilot_id, display)  # a pinned name beats the vote
        real_pilots.append(ResolvedPilot(pilot_id, display, low_confidence=False))
        for d in group:
            deck_pilot[d.deck_id] = pilot_id
        if len(merged) > 1:
            variant_clusters.append(VariantCluster(pilot_id, display, merged))
        multi = _multi_name_id(pilot_id, display, group, names)
        if multi:
            multi_name_ids.append(multi)

    # Null decks: one synthetic, low-confidence pilot per distinct recovered
    # name. A deck whose title yields no name is keyed on its own deck id, so
    # untitled decks stay separate rather than collapsing into one bogus node.
    null_groups: dict[str, tuple[str, list]] = {}
    for deck in (d for g in null.values() for d in g):
        name = _display_name(deck)
        key = f"nan:{name.casefold()}" if name else f"nan:deck:{deck.deck_id}"
        _, group = null_groups.setdefault(key, (name or "unknown", []))
        group.append(deck)
    for key, (display, group) in null_groups.items():
        null_pilots.append(ResolvedPilot(key, display, low_confidence=True))
        for d in group:
            deck_pilot[d.deck_id] = key

    # Display name is the player identity: ids that recovered the same name are
    # one person, so fold them onto a single id before anything downstream runs
    # (ADR 0007). A same-name collision inside one event survives this and is
    # separated again by the event split below.
    pilots, joined_names, name_splits = _join_identical_names(
        real_pilots + null_pilots, deck_pilot, curation
    )
    real_joined = [p for p in pilots if not p.pilot.startswith("nan:")]

    # A resolved pilot with several decks at one event is several people sharing
    # a name; split them so every pilot holds at most one deck per event (ADR 0004).
    # A card-identical pair that only reached one id after the merge or null-join
    # above is one registration, so it collapses here rather than splitting into
    # numbered phantoms (F4).
    pilots, event_splits, late_dropped = _split_event_collisions(
        decks, deck_pilot, pilots, decklists
    )
    if late_dropped:
        gone |= {d.dropped_deck for d in late_dropped}
        decks = [d for d in decks if d.deck_id not in gone]
        dropped = dropped + late_dropped

    # Under-merges are scanned over real pilots only: the null bucket is already
    # surfaced separately, so including it would just double-report the noise.
    # A decided pair is off the review list either way: a rejection is counted in
    # the scan, and a confirmed merge folds an id away before it, so those folded
    # ids present in the data are counted here to complete the "already decided".
    candidates, rejected = _under_merges(real_joined, curation)
    merged = sum(1 for pid in {d.pilot for d in decks} if curation.canonical(pid) != pid)
    # Raw ids plus the buckets they resolve into (the real/null keys are exactly
    # those resolved ids), so a decision keyed on a live canonical is not misread
    # as dead; deck ids are post-dedup (issue #37).
    live_ids = {d.pilot for d in decks} | set(real) | set(null)
    dead = dead_entries(curation, live_ids, {d.deck_id for d in decks})
    report = Reconciliation(
        variant_clusters, candidates, null_pilots, event_splits, dropped,
        joined_names, rejected + merged, dead, multi_name_ids, name_splits,
    )
    return PilotResolution(
        deck_pilot=deck_pilot, pilots=pilots, report=report,
        dropped_decks=frozenset(gone),
    )


def _multi_name_id(
    pilot: str, display: str, group, names: list[str | None]
) -> "MultiNameId | None":
    """A report entry if the id's decks recover >1 surname across >1 event.

    Two surnames confined to a single event are just a same-event collision the
    event split already separates; only surnames recurring across disjoint events
    point to one id reused by two different people, which the majority vote would
    hide (issue #39). ``names`` is the caller's already-recovered name per deck.
    """
    families: set[str] = set()
    events: set[str] = set()
    distinct: set[str] = set()
    for deck, name in zip(group, names):
        if not name:
            continue
        distinct.add(name)
        if family := _surname_family(name):
            families.add(family)
            events.add(deck.event)
    if len(families) > 1 and len(events) > 1:
        return MultiNameId(pilot, display, sorted(distinct))
    return None


def _drop_duplicates(
    decks, decklists: dict[str, object], resolved_id
) -> list[DroppedDuplicate]:
    """Find duplicate registrations: same id, event, name, and identical list.

    Under one-entry-per-player, two decks that share a *resolved* id, an event, a
    recovered name, and a card-for-card identical list are one registration
    entered twice, not two people (teammates share a list but never a name). The
    id is resolved through the dictionary (``resolved_id``) so a copy entered
    under two ids a human has merged is caught here, not split into numbered
    phantoms downstream (F4). The best placement is kept; the rest are returned
    for the log. A pilot's two genuinely different decks at one event are left
    for the collision split.
    """
    groups: dict[tuple, list] = {}
    for deck in decks:
        name = _display_name(deck)
        # A deck with no card signature (none should reach here) keys on its own
        # id, so it never reads as a duplicate of another.
        signature = decklists.get(deck.deck_id, deck.deck_id)
        key = (resolved_id(deck), deck.event, (name or "").casefold(), signature)
        groups.setdefault(key, []).append(deck)

    dropped: list[DroppedDuplicate] = []
    for (pilot, _, _, _), group in groups.items():
        if len(group) >= 2:
            dropped.extend(_pick_survivor(group, pilot)[1])
    return dropped


def _pick_survivor(members, pilot: str) -> tuple[object, list[DroppedDuplicate]]:
    """Keep the best-placed deck of a duplicate group; log the rest as drops.

    Members share a resolved pilot, an event, and a card signature, so they are
    one registration entered more than once. The best placement (deck id to break
    ties) is kept; each other is returned as a :class:`DroppedDuplicate`.
    """
    keep, *extra = sorted(
        members, key=lambda d: (d.placement is None, d.placement or 0, d.deck_id)
    )
    dropped = [
        DroppedDuplicate(
            dropped_deck=d.deck_id, kept_deck=keep.deck_id, pilot=pilot,
            event=keep.event, display_name=_display_name(keep) or keep.deck_id,
        )
        for d in extra
    ]
    return keep, dropped


def _join_identical_names(
    pilots: list[ResolvedPilot], deck_pilot: dict[str, str], curation: Curation
) -> tuple[list[ResolvedPilot], list[JoinedName], list[SplitName]]:
    """Fold pilots that recovered the same display name onto one id (ADR 0007).

    Display name is the primary player identity, so ids resolving to the same
    name (case-insensitively) are one person: a person who registered under two
    spellings that clean up alike, or a real id and the null-bucket orphan of a
    registration whose upstream id was lost. Their decks are repointed onto one
    canonical id -- a real id when the group has one (the busiest, id to break
    ties), never a synthetic ``nan:`` key -- and each join is logged.

    A ``[[split]]`` overrides the join for a name group: two ids declared
    strangers (issue #35) are kept apart, so the group folds into one node per
    split-separated person instead of one node total. Mutates ``deck_pilot`` in
    place and returns the reduced pilot list, the joins, and the splits.
    """
    decks_of: dict[str, list[str]] = {}
    for deck_id, key in deck_pilot.items():
        decks_of.setdefault(key, []).append(deck_id)

    kept: list[ResolvedPilot] = []
    groups: dict[str, list[ResolvedPilot]] = {}
    for p in pilots:
        # An untitled deck yields no name, only the "unknown" placeholder, so it
        # carries no identity to match on and never joins another.
        if p.pilot.startswith("nan:deck:"):
            kept.append(p)
        else:
            groups.setdefault(p.display_name.casefold(), []).append(p)

    def fold(component: list[ResolvedPilot]) -> tuple[ResolvedPilot, JoinedName | None]:
        # One person: repoint every member's decks onto a single canonical id (a
        # real id over a synthetic nan:, busiest to break ties) and log the join.
        canonical = max(
            component,
            key=lambda p: (not p.pilot.startswith("nan:"), len(decks_of.get(p.pilot, ())), p.pilot),
        )
        real = any(not p.pilot.startswith("nan:") for p in component)
        node = ResolvedPilot(canonical.pilot, canonical.display_name, low_confidence=not real)
        for p in component:
            if p.pilot != canonical.pilot:
                for deck_id in decks_of.get(p.pilot, ()):
                    deck_pilot[deck_id] = canonical.pilot
        join = None
        if len(component) > 1:
            join = JoinedName(canonical.display_name, canonical.pilot,
                              sorted(p.pilot for p in component))
        return node, join

    joins: list[JoinedName] = []
    name_splits: list[SplitName] = []
    for group in groups.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        components = _partition_by_split(group, curation)
        nodes = [fold(comp) for comp in components]
        for node, join in nodes:
            kept.append(node)
            if join:
                joins.append(join)
        if len(components) > 1:
            name_splits.append(
                SplitName(group[0].display_name, sorted(node.pilot for node, _ in nodes))
            )
    return kept, joins, name_splits


def _partition_by_split(
    group: list[ResolvedPilot], curation: Curation
) -> list[list[ResolvedPilot]]:
    """Partition one identical-name group by any curated splits or rejects.

    Absent any separator the whole group is one person (ADR 0007). A ``[[split]]``
    declares two of its ids strangers (issue #35); a ``[[reject]]`` records the
    same "different people" judgement from the merge-candidate review (issue #74).
    Either keeps a pair apart: names are not stable identity, so a rejected pair
    one edit apart can converge on a later fetch, and the join must not then fuse
    ids a human recorded as distinct. The group is partitioned: every pair is
    unioned *unless* a split or reject keeps it apart. A separated pair that still
    lands in one component (a third id transitively rejoins them) is
    under-specified, raised rather than silently re-fused -- the trust hole this
    closes.

    Only real ids take part: a synthetic ``nan:`` key is unstable and cannot be
    named in a split (ADR 0009), so once a name is separated, a null-bucket orphan
    of that name cannot be attributed to either side. It becomes its own
    low-confidence node rather than transitively re-fusing the separation (which
    would abort the build) or silently attaching to one side.
    """
    real_ids = [p.pilot for p in group if not p.pilot.startswith("nan:")]
    parent = {i: i for i in real_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def apart(a: str, b: str) -> bool:
        return curation.is_split(a, b) or curation.is_rejected(a, b)

    separated_here = False
    for i, a in enumerate(real_ids):
        for b in real_ids[i + 1:]:
            if apart(a, b):
                separated_here = True
            else:
                parent[find(a)] = find(b)
    if not separated_here:
        return [group]  # untouched: the ADR-0007 single-person fold

    for i, a in enumerate(real_ids):
        for b in real_ids[i + 1:]:
            if apart(a, b) and find(a) == find(b):
                raise CurationError(
                    f"[[split]]/[[reject]] of {a!r} and {b!r} "
                    f"(both {group[0].display_name!r}) is under-specified: another "
                    "real id transitively rejoins them; separate that id from one "
                    "side too"
                )

    by_root: dict[str, list[ResolvedPilot]] = {}
    for p in group:
        # A nan: orphan can join no one once the name is split, so it keys on
        # itself and stands alone; real ids key on their union-find root.
        root = p.pilot if p.pilot.startswith("nan:") else find(p.pilot)
        by_root.setdefault(root, []).append(p)
    return list(by_root.values())


def _split_event_collisions(
    decks, deck_pilot: dict[str, str], pilots: list[ResolvedPilot],
    decklists: dict[str, object] | None,
) -> tuple[list[ResolvedPilot], list[EventSplit], list[DroppedDuplicate]]:
    """Split any pilot that entered one event more than once into numbered people.

    A deck is one pilot's single entry at one event, so two decks under the same
    resolved pilot at the same event are two people who share a name, not one
    person with two lists (ADR 0004). This applies to every pilot uniformly: its
    decks are threaded into careers by card-set similarity (issue #34, ADR 0010),
    so alike decks share a career and each career holds at most one deck per
    event. There are exactly as many careers as the pilot's deepest same-event
    collision, so a pilot with no collision stays one node and only genuine
    duplicates spin off into "<name> 2", "<name> 3", ... Careers are numbered by
    their earliest deck, so threading is append-stable: stable input yields the
    same careers and a newly ingested deck joins its thread without renumbering
    the others. The split is an inference the data cannot confirm, so every
    identity in a split family is marked low confidence.

    Before splitting, a card-identical pair under one resolved id at one event is
    collapsed, not numbered: it only reached one id after a merge or the null-bucket
    join, so it is one registration entered twice, not two people (F4). The drops
    are removed from ``deck_pilot`` and returned for the log.

    Mutates ``deck_pilot`` in place to point split decks at their new pilot key,
    and returns the rebuilt pilot list, the splits, and any collapsed duplicates
    for the reconciliation report.
    """
    grouped: dict[str, list] = {}
    for deck in decks:
        grouped.setdefault(deck_pilot[deck.deck_id], []).append(deck)

    by_key = {p.pilot: p for p in pilots}
    resolved: dict[str, ResolvedPilot] = {}
    splits: list[EventSplit] = []
    dropped: list[DroppedDuplicate] = []

    for key, group in grouped.items():
        if decklists:
            group, extra = _collapse_identical(group, key, decklists)
            for d in extra:
                del deck_pilot[d.dropped_deck]
            dropped.extend(extra)
        slot_of = _thread_careers(group, decklists)
        people_count = max(slot_of.values()) + 1
        base = by_key[key]
        if people_count == 1:
            resolved[key] = base
            continue
        people = []
        for slot in range(people_count):
            pkey = key if slot == 0 else f"{key}#{slot + 1}"
            name = f"{base.display_name} {slot + 1}"
            resolved[pkey] = ResolvedPilot(pkey, name, low_confidence=True)
            people.append(name)
        splits.append(EventSplit(base.display_name, people))
        for deck in group:
            slot = slot_of[deck.deck_id]
            deck_pilot[deck.deck_id] = key if slot == 0 else f"{key}#{slot + 1}"

    return list(resolved.values()), splits, dropped


def _collapse_identical(
    group, pilot: str, decklists: dict[str, object]
) -> tuple[list, list[DroppedDuplicate]]:
    """Drop card-identical copies unified from different ids at one event (F4).

    Two decks with an identical list at one event that reached this one resolved
    pilot from *different* upstream ids were unified by a merge or the null-bucket
    join: one registration entered twice, so the worse placement is dropped. Decks
    that share an upstream id are left for the event split instead -- teammates
    share a list but never a name, so a same-id collision is two people, not a
    copy (same-id, same-name copies were already dropped before resolution).
    """
    by_sig: dict[tuple, list] = {}
    for deck in group:
        signature = decklists.get(deck.deck_id, deck.deck_id)
        by_sig.setdefault((deck.event, signature), []).append(deck)

    kept, dropped = [], []
    for members in by_sig.values():
        keep = min(
            members, key=lambda d: (d.placement is None, d.placement or 0, d.deck_id)
        )
        # Same-id decks are kept (a collision the split handles); copies unified
        # from another id are dropped against the best-placed survivor.
        kept.extend(d for d in members if d.pilot == keep.pilot)
        copies = [d for d in members if d.pilot != keep.pilot]
        if copies:
            dropped.extend(_pick_survivor([keep, *copies], pilot)[1])
    return kept, dropped


def _thread_careers(decks, decklists: dict[str, object] | None) -> dict[str, int]:
    """Thread a resolved pilot's decks into careers by card-set similarity.

    A career is one person: one entry per event, playing a recognisable deck
    over time. Decks are grouped so alike decks share a career and each career
    holds at most one deck per event. Events are threaded oldest first by
    registration time (``created_at``); at each event every deck joins the career
    it most overlaps, and a deck with no career free (the event runs deeper than
    the careers so far) opens a new one. The number of careers therefore equals
    the pilot's deepest same-event collision: a pilot with no collision threads
    into a single career, untouched.

    Careers are numbered by their earliest deck's registration time. That anchor
    is what makes the threading append-stable: a later-registered deck is assigned
    after the decks already there and can neither displace an incumbent from its
    career nor move a career's anchor. Stable input therefore yields the same
    careers and a backfilled deck joins its thread without renumbering the others.
    Deck ids are random Moxfield GUIDs carrying no order (issue #68), so ordering
    on them was not append-stable at all: a backfilled deck whose GUID sorted low
    became a career's anchor and renumbered the family.

    ``created_at`` is not a total order -- date-only stamps and organiser bulk
    uploads leave decks sharing one instant -- so the deck id is the secondary
    tie-break. For those ties threading falls back to deck-id order, i.e. exactly
    the un-stable behaviour above; this restores append-stability for the large
    majority of affected data, not all of it. Returns each deck id's career index
    (0-based, anchor-ordered).
    """
    def cards(deck_id: str) -> frozenset:
        sig = decklists.get(deck_id) if decklists else None
        return _card_set(sig) if sig is not None else frozenset()

    card_of = {deck.deck_id: cards(deck.deck_id) for deck in decks}
    # Registration time, deck id as the tie-break (issue #68). Threading anchors
    # on this so a backfilled deck files by when it was registered, not by where
    # its random GUID happens to sort.
    when = {deck.deck_id: (deck.created_at, deck.deck_id) for deck in decks}
    by_event: dict[str, list] = {}
    for deck in decks:
        by_event.setdefault(deck.event, []).append(deck)

    careers: list[list[str]] = []  # each career is its member deck ids, oldest first
    for event in sorted(by_event, key=lambda e: min(when[d.deck_id] for d in by_event[e])):
        _assign_event(sorted(by_event[event], key=lambda d: when[d.deck_id]), careers, card_of, when)

    order = sorted(range(len(careers)), key=lambda i: min(when[did] for did in careers[i]))
    rank = {orig: new for new, orig in enumerate(order)}
    return {did: rank[i] for i, ids in enumerate(careers) for did in ids}


def _assign_event(event_decks, careers: list[list[str]], card_of: dict[str, frozenset],
                  when: dict[str, tuple]) -> None:
    """Assign one event's decks to distinct careers by card overlap.

    Each deck joins the available career it most overlaps (max card-set Jaccard
    against a member deck, ADR 0005, so a career's signature does not dilute as it
    grows). A career already holding a deck from this event is not available,
    keeping one deck per event per career.

    Two cases, because the split deepens only at a *seeding* event (one with more
    decks than there are careers so far):

    - Not seeding (decks fit inside the existing careers): take decks oldest first
      by registration time, each claiming its best free career. Oldest-first keeps
      a later-registered deck append stable -- assigned after the decks already
      there, it cannot bump an incumbent off its career (ADR 0010). The caller
      passes ``event_decks`` already in registration order.
    - Seeding: the best-fitting decks claim the existing (accumulated) careers, and
      the leftover decks open the new ones. Here oldest-first would instead hand an
      accumulated career to whichever colliding deck registered first, stranding
      the deck that actually continues that history on a fresh career. There is no
      incumbent to protect on the new careers, so best-fit is safe (ADR 0010).

    Ties on overlap go to the earliest-registered deck, then the smallest deck id,
    then the earliest career, for determinism. Registration time (``when``, a
    ``(created_at, deck_id)`` pair) drives both the seeding sort and the tie-break,
    matching the anchor order in ``_thread_careers`` (issue #68); when two decks
    share a ``created_at`` the deck id decides, the residual limitation noted there.
    """
    n_existing = len(careers)
    if len(event_decks) <= n_existing:
        taken: set[int] = set()
        for deck in event_decks:
            best, best_overlap = 0, -1.0
            for ci in range(n_existing):
                if ci in taken:
                    continue
                overlap = max((_jaccard(card_of[deck.deck_id], card_of[m]) for m in careers[ci]), default=0.0)
                if overlap > best_overlap:
                    best, best_overlap = ci, overlap
            careers[best].append(deck.deck_id)
            taken.add(best)
        return

    scored = [
        (max((_jaccard(card_of[deck.deck_id], card_of[m]) for m in careers[ci]), default=0.0),
         when[deck.deck_id], ci)
        for deck in event_decks for ci in range(n_existing)
    ]
    scored.sort(key=lambda s: (-s[0], s[1], s[2]))
    claimed: dict[str, int] = {}
    taken_careers: set[int] = set()
    for _, (_created, deck_id), ci in scored:
        if deck_id in claimed or ci in taken_careers:
            continue
        claimed[deck_id] = ci
        taken_careers.add(ci)
    for deck in event_decks:
        if deck.deck_id in claimed:
            careers[claimed[deck.deck_id]].append(deck.deck_id)
        else:
            careers.append([deck.deck_id])


def _card_set(signature) -> frozenset:
    """Flatten a deck signature into one card set for overlap scoring.

    The build keys a deck on a (main, side) pair of frozensets; a deck is a set
    of cards in the singleton format, so the two boards union into one set. A
    plain tuple of card names (used in tests) flattens the same way.
    """
    cards: set = set()
    for part in signature:
        cards |= part if isinstance(part, (set, frozenset)) else {part}
    return frozenset(cards)


def _jaccard(a: frozenset, b: frozenset) -> float:
    """Card-set overlap: shared cards over total (ADR 0005). Empty sets score 0."""
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _choose_display_name(
    names: list[str | None], *, fallback: str
) -> tuple[str, dict[str, int]]:
    """Pick a display name by fuzzy-clustered majority vote.

    Returns the winning name and the spelling variants (name -> count) that
    consolidated into its cluster. Falls back to the pilot id when no title
    yielded a name.
    """
    counts = Counter(n for n in names if n)
    if not counts:
        return fallback, {}

    # Greedy clusters, seeding from the most common names so a cluster's
    # representative is its dominant spelling.
    clusters: list[dict[str, int]] = []
    for name, count in counts.most_common():
        for cluster in clusters:
            representative = next(iter(cluster))
            if _similar(name, representative):
                cluster[name] = count
                break
        else:
            clusters.append({name: count})

    # Ties are broken on the name string, never on cluster/deck order, so a
    # re-export that reorders the source cannot flip the resolved identity (F7).
    ranked = [(c, _cluster_display(c)) for c in clusters]
    winner, display = max(ranked, key=lambda cd: (sum(cd[0].values()), cd[1]))
    return display, dict(winner)


def _cluster_display(cluster: dict[str, int]) -> str:
    """The cluster's representative spelling: most-used, name string to break ties."""
    return max(cluster, key=lambda n: (cluster[n], n))


def _under_merges(
    pilots: list[ResolvedPilot], curation: Curation
) -> tuple[list[UnderMerge], int]:
    """Uncurated pilot-id pairs whose names say they may be one person.

    Two ids are only ever a candidate here, never merged by this function: the
    build merges only what the dictionary records (:mod:`graph7ph.curation`).
    Returns the live candidates and a count of pairs a human has *rejected* as
    two people, which drop off the list and are counted so the review shrinks as
    decisions accrue. Confirmed merges never reach here (they were collapsed onto
    one id upstream), so the caller counts those separately (issue #9).
    """
    names = {p.pilot: p.display_name for p in pilots}
    candidates: list[UnderMerge] = []
    rejected = 0
    for a, b in sorted(_candidate_pairs(names)):
        relation = name_relation(names[a], names[b])
        if not relation:
            continue
        if curation.is_rejected(a, b):
            rejected += 1
        else:
            candidates.append(UnderMerge(names[a], [a, b], relation))
    return candidates, rejected


def _candidate_pairs(names: dict[str, str]) -> set[tuple[str, str]]:
    """Pairs worth asking :func:`name_relation` about.

    Every relation needs the two names to share a surname initial or a first
    name, so indexing on those two keys finds every real pair while keeping the
    scan off the full 1248-by-1248 square.
    """
    by_initial: dict[str, list[str]] = {}
    by_first: dict[str, list[str]] = {}
    for pilot_id, name in names.items():
        regular, handle = _FIRST_INITIAL.match(name), _HANDLE.match(name)
        if regular:
            # Index on the surname's first letter, so a plain initial and a
            # hyphenated one ("K", "KH", "K-H") share a bucket and get compared.
            by_initial.setdefault(regular.group(2)[0].casefold(), []).append(pilot_id)
            by_first.setdefault(regular.group(1).casefold(), []).append(pilot_id)
        elif handle:
            by_initial.setdefault(handle.group(2).casefold(), []).append(pilot_id)
        else:
            # A bare first name ("Noelle") or something opaque ("alejandrofp"),
            # which can only ever meet a name whose first part matches it.
            by_first.setdefault(name.casefold(), []).append(pilot_id)

    return {
        (a, b)
        for group in (*by_initial.values(), *by_first.values())
        for i, a in enumerate(group)
        for b in group[i + 1:]
    }


# A name shaped "<first...> <surname>" ("Jordan C", "Chris K-H"), the regular
# form the source's titles mostly follow. The surname is the whole final token,
# so a hyphenated double-barrel initial ("K-H") is captured as one unit rather
# than clipped to its last letter.
_FIRST_INITIAL = re.compile(r"^(.+)\s+([^\s]+)$")


def _surname_family(name: str) -> str | None:
    """The surname identity of a recovered name, or ``None`` if it has no surname.

    A bare first name or an opaque handle carries no surname to compare, so it
    cannot mark an id as spanning two families (issue #39).
    """
    m = _FIRST_INITIAL.match(name)
    return _surname_key(m.group(2)) if m else None


def _surname_key(surname: str) -> str:
    """A surname's identity for matching: letters and digits only, casefolded.

    Collapses punctuation and spacing so a hyphenated initial and its unspaced
    form read alike ("K-H" == "KH"), while a plain initial stays distinct from a
    longer surname ("K" != "KH", "C" != "Cook").
    """
    return re.sub(r"[^a-z0-9]", "", surname.casefold())

# A handle the title carries in place of a name: the first three letters of a
# first name, the surname initial, and an optional per-registration number
# ("OdeB", "CalT13", "JonB48"). The number counts registrations, not people, so
# it is never part of the identity.
_HANDLE = re.compile(r"^([A-Za-z]{3})([A-Z])(\d*)$")

# One slip of the fingers apart: a transposition, or one letter added or
# dropped. See :func:`_edits` for why the threshold cannot be loosened.
_TYPO_EDITS = 1


def name_relation(a: str, b: str) -> str | None:
    """How two recovered names relate, or ``None`` if they do not.

    Each relation is a class of evidence the data carries on its own, in
    descending confidence, and every one is only ever surfaced as an under-merge
    candidate for a human to decide -- none is auto-applied. The heuristics merge
    nothing; only the curation dictionary does (issue #9). The shape alone cannot
    tell "Chris"/"Christopher" (one person) from "Joe"/"Joel" (two), nor
    "Cordel"/"Cordell" (one) from "Ramona"/"Damon" (two), and deck overlap cannot
    break the tie either (teammates play each other's lists). ``exact`` reaches
    the sole caller (:func:`_under_merges`) only for a rejected pair whose names
    have converged: the join folds every other case-only duplicate upstream, but
    keeps a ``[[reject]]`` pair apart (issue #74), and :func:`_under_merges`
    counts that pair as rejected rather than listing it as a candidate.
    """
    if a.casefold() == b.casefold():
        return "exact"  # differs only in case: "Nathan S" / "Nathan s"
    ma, mb = _FIRST_INITIAL.match(a), _FIRST_INITIAL.match(b)
    if ma and mb:
        if _surname_key(ma.group(2)) != _surname_key(mb.group(2)):
            return None  # "Jordan C" / "Jordan B": different surnames.
        first_a, first_b = ma.group(1).casefold(), mb.group(1).casefold()
        if first_a.startswith(first_b) or first_b.startswith(first_a):
            return "nickname"  # "Alex J" / "Alexander J"
        if _edits(first_a, first_b) <= _TYPO_EDITS:
            return "typo"  # "Alexadner J" / "Alexander J"
        return None
    # A bare first name against a regular name: "Noelle" / "Noelle T".
    for bare, full in ((a, mb), (b, ma)):
        if full and not _FIRST_INITIAL.match(bare) and (
            bare.casefold() == full.group(1).casefold()
        ):
            return "first-name"
    # A handle against a regular name: "OdeB" / "Oden B". The handle carries a
    # single surname initial, so it only decodes against a single-initial surname.
    for handle, full in ((a, mb), (b, ma)):
        h = _HANDLE.match(handle)
        if full and h and len(full.group(2)) == 1 and (
            h.group(1).casefold() == full.group(1)[:3].casefold()
            and h.group(2).casefold() == full.group(2).casefold()
        ):
            return "handle"
    return None


def _edits(a: str, b: str) -> int:
    """Damerau-Levenshtein distance, counting a transposition as one edit.

    Transposition is what makes this honest about names. Every mistype in the
    source is one slip of the fingers -- "Brnadon"/"Brandon", "Jodran"/"Jordan",
    "Alexadner"/"Alexander", "Daneil"/"Daniel" transpose a pair; "Tristian",
    "Cordell", "Michel", "Zachaery" add or drop one letter -- so all of them sit
    at distance 1. Counting a transposition as two edits instead would force the
    threshold up to 2, which on names this short also admits "Jake"/"Jack" and
    "Dan"/"Sam", who are different people.
    """
    grid = {(i, -1): i + 1 for i in range(-1, len(a))}
    grid.update({(-1, j): j + 1 for j in range(-1, len(b))})
    for i, ca in enumerate(a):
        for j, cb in enumerate(b):
            grid[i, j] = min(
                grid[i - 1, j] + 1,                      # delete
                grid[i, j - 1] + 1,                      # insert
                grid[i - 1, j - 1] + (ca != cb),         # substitute
            )
            if i and j and ca == b[j - 1] and a[i - 1] == cb:
                grid[i, j] = min(grid[i, j], grid[i - 2, j - 2] + 1)  # transpose
    return grid[len(a) - 1, len(b) - 1]


def _similar(a: str, b: str) -> bool:
    """Whether two recovered names are spelling variants of one person.

    For "<first...> <surname initial>" names the surname initial must match,
    then the first-name parts must be nickname- or typo-close. Everything else
    falls back to a whole-string ratio.
    """
    ta, tb = a.casefold().split(), b.casefold().split()
    if _has_initial(ta) and _has_initial(tb):
        if ta[-1] != tb[-1]:
            return False  # "Jordan C" vs "Jordan B": different people.
        first_a, first_b = " ".join(ta[:-1]), " ".join(tb[:-1])
        return (
            first_a.startswith(first_b)  # "Dan" / "Daniel"
            or first_b.startswith(first_a)
            or _ratio(first_a, first_b) >= _FIRST_NAME_THRESHOLD  # typos
        )
    return _ratio(a, b) >= _FUZZY_THRESHOLD


def _has_initial(tokens: list[str]) -> bool:
    return len(tokens) >= 2 and len(tokens[-1]) == 1


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio()
