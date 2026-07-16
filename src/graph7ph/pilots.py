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
from dataclasses import dataclass
from difflib import SequenceMatcher

from graph7ph.curation import Curation
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
    for segment in _SEPARATOR.split(stripped):
        name = segment.strip()
        if not name:
            continue
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
NULL_PILOT_IDS = frozenset({"nan"})

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
class EventSplit:
    """One pilot id that entered an event more than once, split into people.

    A deck is one pilot's single entry at one event, so the duplicates were
    distinct people sharing a name; ``people`` are the numbered identities they
    were split into (ADR 0004)."""

    display_name: str
    people: list[str]


@dataclass(frozen=True)
class Reconciliation:
    variant_clusters: list[VariantCluster]
    under_merges: list[UnderMerge]  # UNCURATED candidates only; curated ones drop off
    null_pilots: list[ResolvedPilot]  # the re-keyed null bucket (ADR 0004)
    event_splits: list[EventSplit]  # ids split for same-event collisions (ADR 0004)
    dropped_duplicates: list[DroppedDuplicate]  # removed duplicate registrations
    joined_names: list[JoinedName]  # ids collapsed for sharing a display name
    curated: int  # decided pairs off the review list: rejections plus applied merges


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
    dropped = _drop_duplicates(decks, decklists) if decklists else []
    gone = {d.dropped_deck for d in dropped}
    decks = [d for d in decks if d.deck_id not in gone]

    deck_pilot: dict[str, str] = {}
    real_pilots: list[ResolvedPilot] = []
    null_pilots: list[ResolvedPilot] = []
    variant_clusters: list[VariantCluster] = []

    # A deck's pilot is its upstream id, unless the dictionary reassigns the deck
    # (a null-pilot deck to its real owner) or merges the id onto a canonical one.
    def resolved_id(deck) -> str:
        return curation.canonical(curation.deck_pilots.get(deck.deck_id, deck.pilot))

    real: dict[str, list] = {}
    null: dict[str, list] = {}
    for deck in decks:
        pid = resolved_id(deck)
        bucket = null if pid in NULL_PILOT_IDS else real
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
    pilots, joined_names = _join_identical_names(real_pilots + null_pilots, deck_pilot)
    real_joined = [p for p in pilots if not p.pilot.startswith("nan:")]

    # A resolved pilot with several decks at one event is several people sharing
    # a name; split them so every pilot holds at most one deck per event (ADR 0004).
    pilots, event_splits = _split_event_collisions(decks, deck_pilot, pilots)

    # Under-merges are scanned over real pilots only: the null bucket is already
    # surfaced separately, so including it would just double-report the noise.
    # A decided pair is off the review list either way: a rejection is counted in
    # the scan, and a confirmed merge folds an id away before it, so those folded
    # ids present in the data are counted here to complete the "already decided".
    candidates, rejected = _under_merges(real_joined, curation)
    merged = sum(1 for pid in {d.pilot for d in decks} if curation.canonical(pid) != pid)
    report = Reconciliation(
        variant_clusters, candidates, null_pilots, event_splits, dropped,
        joined_names, rejected + merged,
    )
    return PilotResolution(
        deck_pilot=deck_pilot, pilots=pilots, report=report,
        dropped_decks=frozenset(gone),
    )


def _drop_duplicates(decks, decklists: dict[str, object]) -> list[DroppedDuplicate]:
    """Find duplicate registrations: same id, event, name, and identical list.

    Under one-entry-per-player, two decks that share an upstream id, an event, a
    recovered name, and a card-for-card identical list are one registration
    entered twice, not two people (teammates share a list but never a name). The
    best placement is kept; the rest are returned for the log. A pilot's two
    genuinely different decks at one event are left for the collision split.
    """
    groups: dict[tuple, list] = {}
    for deck in decks:
        name = _display_name(deck)
        # A deck with no card signature (none should reach here) keys on its own
        # id, so it never reads as a duplicate of another.
        signature = decklists.get(deck.deck_id, deck.deck_id)
        key = (deck.pilot, deck.event, (name or "").casefold(), signature)
        groups.setdefault(key, []).append(deck)

    dropped: list[DroppedDuplicate] = []
    for (pilot, event, _, _), group in groups.items():
        if len(group) < 2:
            continue
        keep, *extra = sorted(
            group, key=lambda d: (d.placement is None, d.placement or 0, d.deck_id)
        )
        for d in extra:
            dropped.append(DroppedDuplicate(
                dropped_deck=d.deck_id, kept_deck=keep.deck_id, pilot=pilot,
                event=event, display_name=_display_name(keep) or keep.deck_id,
            ))
    return dropped


def _join_identical_names(
    pilots: list[ResolvedPilot], deck_pilot: dict[str, str]
) -> tuple[list[ResolvedPilot], list[JoinedName]]:
    """Fold pilots that recovered the same display name onto one id (ADR 0007).

    Display name is the primary player identity, so ids resolving to the same
    name (case-insensitively) are one person: a person who registered under two
    spellings that clean up alike, or a real id and the null-bucket orphan of a
    registration whose upstream id was lost. Their decks are repointed onto one
    canonical id -- a real id when the group has one (the busiest, id to break
    ties), never a synthetic ``nan:`` key -- and each join is logged. Mutates
    ``deck_pilot`` in place and returns the reduced pilot list and the joins.
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

    joins: list[JoinedName] = []
    for group in groups.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        canonical = max(
            group,
            key=lambda p: (not p.pilot.startswith("nan:"), len(decks_of.get(p.pilot, ())), p.pilot),
        )
        real = any(not p.pilot.startswith("nan:") for p in group)
        kept.append(ResolvedPilot(canonical.pilot, canonical.display_name,
                                  low_confidence=not real))
        for p in group:
            if p.pilot != canonical.pilot:
                for deck_id in decks_of.get(p.pilot, ()):
                    deck_pilot[deck_id] = canonical.pilot
        joins.append(JoinedName(canonical.display_name, canonical.pilot,
                                sorted(p.pilot for p in group)))
    return kept, joins


def _split_event_collisions(
    decks, deck_pilot: dict[str, str], pilots: list[ResolvedPilot]
) -> tuple[list[ResolvedPilot], list[EventSplit]]:
    """Split any pilot that entered one event more than once into numbered people.

    A deck is one pilot's single entry at one event, so two decks under the same
    resolved pilot at the same event are two people who share a name, not one
    person with two lists (ADR 0004). This applies to every pilot uniformly: for
    each one, its decks at each event are ordered (best placement first, deck id
    to break ties) and dealt out one per identity, so identity 1 keeps a full
    one-per-event record and only genuine same-event duplicates spin off into
    "<name> 2", "<name> 3", ... The split is an inference the data cannot
    confirm, so every identity in a split family is marked low confidence.

    Mutates ``deck_pilot`` in place to point split decks at their new pilot key,
    and returns the rebuilt pilot list and the splits for the reconciliation report.
    """
    grouped: dict[str, list] = {}
    for deck in decks:
        grouped.setdefault(deck_pilot[deck.deck_id], []).append(deck)

    by_key = {p.pilot: p for p in pilots}
    resolved: dict[str, ResolvedPilot] = {}
    splits: list[EventSplit] = []

    for key, group in grouped.items():
        slot_of = _event_slots(group)
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

    return list(resolved.values()), splits


def _event_slots(decks) -> dict[str, int]:
    """Deal each deck a slot so no two decks at one event share one.

    Within each event the decks are ordered (best placement first, deck id to
    break ties) and given slots 0, 1, 2..., so a pilot's single entries all land
    in slot 0 and only true same-event collisions reach into 1 and beyond.
    """
    by_event: dict[str, list] = {}
    for deck in decks:
        by_event.setdefault(deck.event, []).append(deck)
    slot_of: dict[str, int] = {}
    for event_decks in by_event.values():
        event_decks.sort(
            key=lambda d: (d.placement is None, d.placement or 0, d.deck_id)
        )
        for slot, deck in enumerate(event_decks):
            slot_of[deck.deck_id] = slot
    return slot_of


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

    winner = max(clusters, key=lambda c: (sum(c.values()), -_rank(c, counts)))
    display = max(winner, key=lambda n: (winner[n], n == _mode(counts)))
    return display, dict(winner)


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

# Relations strong enough to stand on their own. The other two only ever propose:
# the shape cannot tell "Chris"/"Christopher" (one person) from "Joe"/"Joel"
# (two), nor "Cordel"/"Cordell" (one) from "Ramona"/"Damon" (two). Deck overlap
# cannot break the tie either, since teammates play each other's lists, so those
# go to a human and are remembered in the dictionary.
DECIDED_RELATIONS = frozenset({"exact", "first-name", "handle"})


def name_relation(a: str, b: str) -> str | None:
    """How two recovered names relate, or ``None`` if they do not.

    Each relation is a class of evidence the data carries on its own, in
    descending confidence. ``exact`` and ``first-name`` and ``handle`` are
    decided; ``nickname`` and ``typo`` only ever propose, because the shape
    cannot tell "Chris"/"Christopher" (one person) from "Joe"/"Joel" (two), nor
    "Cordel"/"Cordell" (one) from "Ramona"/"Damon" (two).
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


def _mode(counts: Counter) -> str:
    return counts.most_common(1)[0][0]


def _rank(cluster: dict[str, int], counts: Counter) -> int:
    """Position of the cluster's top spelling in the overall order (tie-break)."""
    ordered = [n for n, _ in counts.most_common()]
    return min(ordered.index(n) for n in cluster)
