"""Curated pilot identity decisions: the growing dictionary (issue #9, ADR 0004).

The heuristics in :mod:`graph7ph.pilots` resolve what the data can decide on its
own. This module records the decisions it cannot, so they survive a rebuild.

Every entry keys on a *stable upstream identifier*: a pilot id or a deck id, both
of which come from the source and never change. Nothing keys on a derived value
-- not a display name (a majority vote that new decks can flip), not a synthetic
key like ``nan:darcy`` or ``LunarRedFalcon#2`` (which the resolution mints). That
is what makes a decision timeless: recorded once, it applies identically to every
future ingestion, so the pipeline always runs in the same direction.

The file is TOML because a human writes it: it takes comments, and ``tomllib`` is
in the standard library, so reading it costs no dependency.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# The eight kinds of recorded decision, one per TOML entry type (repo idiom:
# ingest.py `FlagKind`). A dead entry names which kind quietly stopped firing.
DeadKind = Literal[
    "merge", "reject", "split", "name", "deck_pilot", "deck_archetype",
    "deck_event", "hold",
]

# The dictionary is checked in, and lives apart from `snapshots/` (immutable
# source) and `data/` (derived artifacts): it is neither, it is human judgement.
CURATION_PATH = Path("curation/pilots.toml")

# A snapshot directory's name, as `fetch` mints it ("%Y%m%dT%H%M%SZ"). A
# [[hold]]'s `as_of` is graded on this shape and not on whether the directory is
# present, because presence is not a portable question: `snapshots/` is
# gitignored and each directory is named for the wall clock of the machine that
# fetched it, so the stamps differ in every clone and CI holds none at all. A
# dictionary that loaded only where it was written would make the one entry kind
# that applies nothing to the graph the only one able to stop the graph being
# built. Whether a stamp names a snapshot a given build actually read is a real
# question, answerable only at build time, and it belongs to issue #230.
SNAPSHOT_STAMP = re.compile(r"^\d{8}T\d{6}Z$")


class CurationError(Exception):
    """The dictionary is malformed, or contradicts itself."""


@dataclass(frozen=True)
class ArchetypeOverride:
    """A corrected archetype classification for one deck, keyed on its deck id.

    The source tags a deck's archetype off its title, so a mistitled list lands
    on the wrong archetype. This replaces that classification with the
    human-confirmed one: a display name and the single engine the deck belongs
    to (its code and label).
    """

    deck_name: str
    engine: str  # the engine code, e.g. "engine:izzet_prowess"
    engine_label: str  # its display label, e.g. "Izzet Prowess"


@dataclass(frozen=True)
class Hold:
    """A pair a human examined and deliberately left undecided (issue #228).

    Every other kind records a decision. This one records that a decision was
    attempted and could not be reached on the evidence available, which is the
    difference between a pair nobody has looked at and a pair that cannot yet be
    settled. Reasoning stays in the TOML comment above the entry, where a human
    reads it; the two fields here are what a later pass needs mechanically.

    ``settles_on`` names the evidence that would decide the pair (a shared event,
    a fresh deck, a corroborating name). It is carried into the report and never
    consulted: the build evaluates every trigger against every hold, because the
    evidence arriving is a fact about the data rather than about what a human
    guessed would settle the pair (issue #230, ADR 0005). ``as_of`` is the
    snapshot the reasoning was written against, and is what the deck-delta
    trigger measures "since" from.
    """

    settles_on: str
    as_of: str  # a snapshot directory name, e.g. "20260815T140746Z"


@dataclass(frozen=True)
class DeadEntry:
    """A recorded decision that matches no id or deck in the current snapshot.

    The dictionary is append-only and outlives the snapshots it was written
    against, so a typo'd id or an upstream-reissued pseudonym leaves an entry
    that silently fires nothing (ADR 0005). Surfaced in the reconciliation
    report -- never fatal -- so a maintainer can retire or repair it.
    """

    kind: DeadKind
    key: str  # the pilot id, deck id or snapshot stamp the build does not hold
    detail: str  # what the dead entry was trying to do


@dataclass(frozen=True)
class Curation:
    """Human decisions the build applies on every rebuild.

    ``merges`` maps an upstream pilot id onto the canonical id it belongs to,
    already flattened transitively, so ``Alex J -> Alexander J -> Alexadner J``
    resolves in one lookup. ``rejected`` holds id pairs judged *not* to be the
    same person, which suppresses them from the candidate report for good.
    ``names`` pins a display name against the majority vote. ``deck_pilots``
    reassigns one deck to an upstream pilot id, which is how a null-pilot deck
    reaches its real owner. ``deck_archetypes`` reclassifies one deck whose
    source title mislabelled its archetype. ``deck_events`` reassigns one deck to
    the event code it was really played at, which is how a deck the source
    stranded at a malformed event rejoins its cohort. ``splits`` holds id pairs
    that share a display name but are different people, which keeps the
    identical-name join (ADR 0007) from folding them into one node -- the
    inverse of a ``merge``. ``holds`` maps an id pair onto the :class:`Hold` a
    human left against it: alone among the eight kinds it decides nothing and
    applies nothing, it only records that the pair was examined (issue #228).

    No pair in ``rejected`` or ``splits`` is one ``merges`` folds together:
    :func:`_refuse_merged_pair` refuses a dictionary that states both. No pair in
    ``holds`` is decided by any of the three: :func:`_refuse_decided_hold` refuses
    a dictionary that calls one pair both open and settled.
    """

    merges: dict[str, str]
    rejected: frozenset[frozenset[str]]
    names: dict[str, str]
    deck_pilots: dict[str, str]
    deck_archetypes: dict[str, ArchetypeOverride] = field(default_factory=dict)
    splits: frozenset[frozenset[str]] = field(default_factory=frozenset)
    deck_events: dict[str, str] = field(default_factory=dict)
    holds: dict[frozenset[str], Hold] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "Curation":
        return cls({}, frozenset(), {}, {}, {}, frozenset(), {}, {})

    def canonical(self, pilot_id: str) -> str:
        """The id ``pilot_id`` was merged into, or itself."""
        return self.merges.get(pilot_id, pilot_id)

    def is_rejected(self, a: str, b: str) -> bool:
        """Whether these two ids were judged to be different people."""
        return frozenset({a, b}) in self.rejected

    def is_split(self, a: str, b: str) -> bool:
        """Whether these two same-named ids were declared different people."""
        return frozenset({a, b}) in self.splits

    def hold(self, *ids: str) -> "Hold | None":
        """The hold recorded against these ids, if a human left one.

        Two ids ask whether they are one person; one id asks whether its own
        several names are one person (issue #232). Both are held by the same kind.
        """
        return self.holds.get(frozenset(ids))


def load_curation(path: Path = CURATION_PATH) -> Curation:
    """Read the dictionary, or return an empty one if it does not exist yet.

    An absent file is not an error: a fresh checkout has made no decisions, and
    the heuristics alone must still build a graph.
    """
    path = Path(path)
    if not path.exists():
        return Curation.empty()
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise CurationError(f"{path} is not valid TOML: {exc}") from exc

    merges = _merges(raw.get("merge", []), path)
    rejected = _pairs(raw.get("reject", []), path, "reject")
    splits = _pairs(raw.get("split", []), path, "split")
    _refuse_merged_pair(merges, rejected, path, "reject")
    _refuse_merged_pair(merges, splits, path, "split")
    holds = _holds(raw.get("hold", []), path)
    _refuse_decided_hold(holds, merges, rejected, splits, path)
    # A hold on one id is matched against the canonical id a merge leaves
    # standing, the id the multi-name queue reports, so holding a folded-away
    # member examines nothing: the canonical stays on the queue as unexamined
    # while the hold's own triggers, which do resolve the merge, can still
    # announce a decision on it (issue #232). The same misaddressing the pin
    # below refuses, and the same fix.
    for ids in holds:
        if len(ids) == 1 and (pilot := next(iter(ids))) in merges:
            raise CurationError(
                f"{path}: [[hold]] holds {pilot!r} on its own several names, "
                f"but it merges into {merges[pilot]!r}; hold the canonical id "
                f"instead"
            )
    names = _names(raw.get("name", []), path)
    # A pin is looked up by the canonical bucket id, so pinning a name on an id
    # that merges away can never fire -- an authoring contradiction, not a dead
    # entry the report can absorb. (`merges` holds only non-canonical members.)
    for pilot in names:
        if pilot in merges:
            raise CurationError(
                f"{path}: [[name]] pins a display name on {pilot!r}, which "
                f"merges into {merges[pilot]!r}; pin the canonical id instead"
            )
    return Curation(
        merges=merges,
        rejected=rejected,
        names=names,
        holds=holds,
        deck_pilots=_deck_map(raw.get("deck_pilot", []), path, "deck_pilot", "pilot"),
        deck_archetypes=_deck_archetypes(raw.get("deck_archetype", []), path),
        splits=splits,
        deck_events=_deck_map(raw.get("deck_event", []), path, "deck_event", "event"),
    )


def dead_entries(
    curation: Curation,
    pilot_ids: set[str],
    deck_ids: set[str],
    snapshot_stamps: set[str] | None = None,
) -> list[DeadEntry]:
    """Recorded decisions that key on an id, deck or snapshot the build lacks.

    ``pilot_ids`` is every id resolution can key on: the raw upstream pilot ids
    *and* the canonical bucket ids they merge into, so a name pinned on a live
    canonical whose members carry all the decks is not misread as dead. Merge
    members are checked against the same set; an absent member fired nothing.
    ``deck_ids`` are post-dedup, so an override stranded on a deduped-away deck
    is caught too. Nothing here raises, since one stale entry among many must
    never break a rebuild. The result is sorted so the report diffs cleanly.

    ``snapshot_stamps`` are the snapshot directories this build actually read, and
    grade a ``[[hold]]``'s ``as_of``. Load time cannot ask that question: the
    stamps differ in every clone and CI holds none, so ``load_curation`` grades
    the shape alone and the real check waits for the build that has an ingested
    set to grade against (issue #230). It lands here rather than as a fault of its
    own because it is the same defect as an absent id -- an entry dated against a
    corpus nobody can reproduce, which no reader can now re-derive the reasoning
    over -- and it carries the same remedy of retiring or refreshing the entry.
    Omitted (``None``) where a caller has no snapshot set, which grades nothing
    rather than calling every ``as_of`` stale.
    """
    dead: list[DeadEntry] = []
    for member, canon in curation.merges.items():
        if member not in pilot_ids:
            dead.append(DeadEntry("merge", member, f"merges into {canon}"))
    # One row per absent id, carrying the partners it was rejected against so a
    # maintainer can find the offending [[reject]] block(s).
    reject_partners: dict[str, set[str]] = {}
    for pair in curation.rejected:
        for pid in pair:
            if pid not in pilot_ids:
                reject_partners.setdefault(pid, set()).update(pair - {pid})
    for pid, partners in reject_partners.items():
        dead.append(DeadEntry("reject", pid, f"rejected against {sorted(partners)}"))
    split_partners: dict[str, set[str]] = {}
    for pair in curation.splits:
        for pid in pair:
            if pid not in pilot_ids:
                split_partners.setdefault(pid, set()).update(pair - {pid})
    for pid, partners in split_partners.items():
        dead.append(DeadEntry("split", pid, f"split from {sorted(partners)}"))
    hold_partners: dict[str, set[str]] = {}
    for ids in curation.holds:
        for pid in ids:
            if pid in pilot_ids:
                continue
            if len(ids) == 1:
                # A hold on one id holds that id's own several names open, so it
                # has no partner to name -- and its own row, since the same id
                # can be held both alone and in a pair, and one row would let
                # retiring the pair leave the other entry dead and unflagged
                # (issue #232).
                dead.append(DeadEntry("hold", pid, "held on its own several names"))
            else:
                hold_partners.setdefault(pid, set()).update(ids - {pid})
    for pid, partners in hold_partners.items():
        dead.append(DeadEntry("hold", pid, f"held against {sorted(partners)}"))
    # One row per absent stamp, not per pair: a hold of three or more ids expands
    # to all of its pairs (:func:`_holds`), which would otherwise put the one
    # stamp in the report three times over.
    stale: dict[str, set[str]] = {}
    for pair, hold in curation.holds.items():
        if snapshot_stamps is not None and hold.as_of not in snapshot_stamps:
            stale.setdefault(hold.as_of, set()).update(pair)
    for as_of, ids in stale.items():
        dead.append(DeadEntry(
            "hold", as_of,
            f"dates the reasoning on {sorted(ids)} against a snapshot this "
            f"build did not read",
        ))
    for pilot, display in curation.names.items():
        if pilot not in pilot_ids:
            dead.append(DeadEntry("name", pilot, f"pins {display!r}"))
    for deck, pilot in curation.deck_pilots.items():
        if deck not in deck_ids:
            dead.append(DeadEntry("deck_pilot", deck, f"reassigns to {pilot}"))
    for deck, override in curation.deck_archetypes.items():
        if deck not in deck_ids:
            dead.append(
                DeadEntry("deck_archetype", deck, f"reclassifies as {override.deck_name}")
            )
    for deck, event in curation.deck_events.items():
        if deck not in deck_ids:
            dead.append(DeadEntry("deck_event", deck, f"reassigns to {event}"))
    return sorted(dead, key=lambda d: (d.kind, d.key))


def _merges(entries: list[dict], path: Path) -> dict[str, str]:
    """Flatten every merge entry into id -> canonical id.

    Merges are transitive: ``Alex J`` merges into ``Alexander J`` and
    ``Alexadner J`` merges into ``Alex J`` must land every one of the three on a
    single pilot. Each entry names its own canonical id, so the entries are
    unioned and every member is then pointed at the canonical id of its group.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    canonicals: list[str] = []
    for entry in entries:
        canonical = entry.get("canonical")
        if not canonical:
            raise CurationError(f"{path}: a [[merge]] entry has no `canonical` id")
        ids = _ids(entry, path, "merge")
        if canonical not in ids:
            raise CurationError(
                f"{path}: [[merge]] canonical {canonical!r} must be one of its own "
                f"`ids` {sorted(ids)}"
            )
        canonicals.append(canonical)
        for pilot_id in ids:
            parent[find(pilot_id)] = find(canonical)

    # A group takes the canonical id declared for it. Two entries that merge into
    # one group while naming different canonical ids are a contradiction, not a
    # preference to silently resolve.
    chosen: dict[str, str] = {}
    for canonical in canonicals:
        root = find(canonical)
        if chosen.setdefault(root, canonical) != canonical:
            raise CurationError(
                f"{path}: merged group holds two canonical ids, "
                f"{chosen[root]!r} and {canonical!r}; they cannot both win"
            )
    return {
        pilot_id: chosen[find(pilot_id)]
        for pilot_id in parent
        if chosen[find(pilot_id)] != pilot_id
    }


def _pairs(entries: list[dict], path: Path, kind: str) -> frozenset[frozenset[str]]:
    """Every pairwise judgement of an all-pairs decision, as size-2 frozensets.

    Both ``reject`` (not the same person) and ``split`` (same name, different
    people) name a set of mutually distinct ids and are tested a pair at a time,
    so an entry of three or more ids is expanded into all of its pairs. A two-id
    entry is just its single pair. (Storing the raw set instead would leave a
    3-id entry matching no pair at all.)
    """
    return frozenset(
        pair for entry in entries for pair in _all_pairs(_ids(entry, path, kind))
    )


def _all_pairs(ids: list[str]) -> set[frozenset[str]]:
    """Every unordered pair of ``ids``."""
    return {frozenset({a, b}) for i, a in enumerate(ids) for b in ids[i + 1:]}


def _holds(entries: list[dict], path: Path) -> dict[frozenset[str], Hold]:
    """Every held id set, pairs expanded the way :func:`_pairs` expands a decision.

    A hold of two or more ids marks them mutually *unsettled*, so three ids leave
    three open pairs rather than one three-way question, and each pair carries
    the same ``settles_on`` and ``as_of``. A hold of *one* id is the other
    identity queue's question and is kept whole: it holds that id's own several
    recovered names open, which is not a question about any pair it belongs to
    (issue #232).

    A pair held twice is refused rather than taken last-wins. A hold exists to be
    revisited, so appending a refreshed entry over an older one is the edit this
    kind invites, and silently keeping the lower block would make the condition
    the report carries a fact about file order rather than about either
    judgement, with the discarded one reported nowhere (``dead_entries`` cannot
    see it, both ids being live). Same reasoning as :func:`_names`, and it fires
    on overlap too, since a 3-id entry and a 2-id entry can share a pair.
    """
    holds: dict[frozenset[str], Hold] = {}
    for entry in entries:
        settles_on, as_of = entry.get("settles_on"), entry.get("as_of")
        if not settles_on or not as_of:
            raise CurationError(
                f"{path}: a [[hold]] entry needs `settles_on` and `as_of`"
            )
        if not SNAPSHOT_STAMP.match(as_of):
            raise CurationError(
                f"{path}: [[hold]] is `as_of` {as_of!r}, which is not a snapshot "
                f"stamp; it dates the reasoning against the corpus it was written "
                f"over, so it has to name a snapshot directory (20260815T140746Z)"
            )
        hold = Hold(settles_on=settles_on, as_of=as_of)
        ids = _ids(entry, path, "hold")
        for pair in ([frozenset(ids)] if len(ids) == 1 else _all_pairs(ids)):
            if pair in holds:
                named = " and ".join(repr(pid) for pid in sorted(pair))
                raise CurationError(
                    f"{path}: [[hold]] holds {named} twice, on "
                    f"{holds[pair].settles_on!r} and {hold.settles_on!r}; file "
                    f"order would decide which the report carries, so keep the "
                    f"entry that supersedes and delete the other"
                )
            holds[pair] = hold
    return holds


def _refuse_decided_hold(
    holds: dict[frozenset[str], Hold],
    merges: dict[str, str],
    rejected: frozenset[frozenset[str]],
    splits: frozenset[frozenset[str]],
    path: Path,
) -> None:
    """Refuse a pair the dictionary both holds and decides.

    A ``[[hold]]`` says a human looked and could not settle the pair; a
    ``[[merge]]``, ``[[reject]]`` or ``[[split]]`` says one did. The two are not
    degrees of the same claim, so no precedence resolves them, and the failure is
    the quiet one :func:`_refuse_merged_pair` describes: whichever kind the code
    consults first, the other is a recorded judgement that fires nothing and that
    ``dead_entries`` cannot report, both ids being live.

    It is also the shape a watchlist invites, since the point of a hold is to be
    upgraded later: the upgrade is editing the ``[[hold]]`` into a decision, not
    appending the decision beside it, so the message names that edit.

    Merges are read off the folded group rather than the entry, since two ids
    chained onto one canonical through separate blocks are as decided as two
    named together. Sorted so the first of several contradictions is stable.

    A hold on a single id has no contradiction to check for. Every kind that
    settles an identity question keys on two ids (``merge``, ``reject``,
    ``split``), so each answers a different question than "are this one id's
    decks one person" (issue #232). What resolves that one is a ``deck_pilot``,
    which keys on a deck and names only the id it moves the deck *to*: which id
    it moves a deck away from is derived at build time rather than stated here,
    and the report shows the id dropping off the queue when it does.
    """
    for pair in sorted(sorted(p) for p in holds if len(p) == 2):
        a, b = pair
        canonical = merges.get(a, a)
        if canonical == merges.get(b, b):
            kind, decided = "merge", f"already folds onto {canonical!r}"
        elif frozenset(pair) in rejected:
            kind, decided = "reject", "already separates them"
        elif frozenset(pair) in splits:
            kind, decided = "split", "already keeps them apart"
        else:
            continue
        raise CurationError(
            f"{path}: [[hold]] leaves {a!r} and {b!r} undecided, but a "
            f"[[{kind}]] {decided}; a pair cannot be both open and settled. "
            f"Delete the [[hold]] -- upgrading one means replacing it with the "
            f"decision, not appending the decision beside it"
        )


def _refuse_merged_pair(
    merges: dict[str, str], pairs: frozenset[frozenset[str]], path: Path, kind: str
) -> None:
    """Refuse a pair a ``[[merge]]`` already folds together.

    A merge states "same person" and a ``[[reject]]`` or ``[[split]]`` states "not
    the same person", so a pair carrying both says both. No precedence resolves it,
    and none is needed: ``[[merge]]`` only ever exists for ids whose recovered names
    differ (all 148 merge groups on file recover different names uncurated, none the
    same), which is the complement of what the identical-name join (ADR 0007) and its
    ``[[split]]`` override decide. The two kinds partition the space.

    So the inverse is inert rather than corrective. The merge has already folded the
    pair, the appended entry moves no deck, and ``dead_entries`` cannot report it
    either, both ids being live -- a reviewer's correction that looks recorded and
    fires nothing. Deleting the wrong ``[[merge]]`` is always sufficient, because the
    join can never re-fuse a pair whose names differ, so the message names that edit.

    Read off the folded group rather than the entry, since two ids chained onto one
    canonical through separate ``[[merge]]`` blocks are as merged as two named
    together. Sorted so the first of several contradictions is always the same one.
    """
    for a, b in sorted(sorted(pair) for pair in pairs):
        canonical = merges.get(a, a)
        if canonical == merges.get(b, b):
            raise CurationError(
                f"{path}: [[{kind}]] holds {a!r} and {b!r}, which a [[merge]] "
                f"already folds onto {canonical!r}; the inverse fires nothing. To "
                f"undo the merge, edit the [[merge]] entry -- do not append a "
                f"[[{kind}]] against it"
            )


def _ids(entry: dict, path: Path, kind: str) -> list[str]:
    """The entry's ``ids``: two or more, and mutually distinct.

    ``hold`` is the one kind that also takes a single id, because the multi-name
    queue asks about one id's own recovered names rather than about a pair
    (issue #232). Every other kind states a relation, which needs two.

    Distinctness is the load-bearing half, not the count. A repeated id (``ids =
    ["A", "A"]``) is a typo that no later stage can catch, because every shape it
    takes loads cleanly and then fires nothing: :func:`_merges` unions an id with
    itself and emits no mapping, and :func:`_pairs` stores a size-1 frozenset that
    :meth:`Curation.is_rejected` and :meth:`Curation.is_split` can never match,
    since both call sites only ever ask about two distinct ids. ``dead_entries``
    cannot report it either, the id being live -- that is why it was written down
    -- so the decision is discarded with a build output identical to never having
    written it. ``["A", "A", "B"]`` is the half-live shape, one real pair stored
    beside one permanently dead singleton, and it is refused rather than partly
    applied: mutual distinctness is what :func:`_pairs` promises (ADR 0009), so it
    is checked here, where the author is still looking at the entry.

    Rejects nothing on file today: 0 of the 208 merge, reject and split entries in
    ``curation/pilots.toml`` repeat an id, and the dictionary loads to an equal
    ``Curation`` either way (183 merges, 35 rejected pairs).
    """
    least = 1 if kind == "hold" else 2
    ids = entry.get("ids")
    if not isinstance(ids, list) or len(ids) < least or len(set(ids)) != len(ids):
        raise CurationError(
            f"{path}: a [[{kind}]] entry needs `ids` of "
            f"{'one' if least == 1 else 'two'} or more distinct ids"
        )
    return ids


def _names(entries: list[dict], path: Path) -> dict[str, str]:
    """Every pin as pilot id -> display name, at most one pin per id.

    A repeated key would be last-wins, which makes the rendered name a fact about
    where the two blocks sit in the file rather than about either decision: reversing
    them renders the other name, and nothing records which was meant. So the repeat
    is refused with both names in the message, since only the author can say which
    one supersedes (issue #204).
    """
    names: dict[str, str] = {}
    for entry in entries:
        pilot, display = entry.get("pilot"), entry.get("display_name")
        if not pilot or not display:
            raise CurationError(f"{path}: a [[name]] entry needs `pilot` and `display_name`")
        if pilot in names:
            raise CurationError(
                f"{path}: [[name]] pins two display names on {pilot!r}, "
                f"{names[pilot]!r} and {display!r}; file order would decide which "
                f"renders, so keep the one that supersedes and delete the other"
            )
        names[pilot] = display
    return names


def _deck_map(entries: list[dict], path: Path, kind: str, target: str) -> dict[str, str]:
    """A deck-keyed decision's ``deck -> target`` map.

    Both ``deck_pilot`` and ``deck_event`` say "this deck belongs to that", and
    differ only in what ``that`` is: the upstream pilot id who registered it, or
    the event code it was played at. Half an entry decides nothing, so either
    half missing is refused where the author can still see it.
    """
    decks: dict[str, str] = {}
    for entry in entries:
        deck, value = entry.get("deck"), entry.get(target)
        if not deck or not value:
            raise CurationError(f"{path}: a [[{kind}]] entry needs `deck` and `{target}`")
        decks[deck] = value
    return decks


def _deck_archetypes(entries: list[dict], path: Path) -> dict[str, ArchetypeOverride]:
    decks: dict[str, ArchetypeOverride] = {}
    for entry in entries:
        deck = entry.get("deck")
        deck_name = entry.get("deck_name")
        engine = entry.get("engine")
        engine_label = entry.get("engine_label")
        if not (deck and deck_name and engine and engine_label):
            raise CurationError(
                f"{path}: a [[deck_archetype]] entry needs `deck`, `deck_name`, "
                "`engine`, and `engine_label`"
            )
        decks[deck] = ArchetypeOverride(
            deck_name=deck_name, engine=engine, engine_label=engine_label
        )
    return decks
