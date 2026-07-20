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

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# The six kinds of recorded decision, one per TOML entry type (repo idiom:
# ingest.py `FlagKind`). A dead entry names which kind quietly stopped firing.
DeadKind = Literal["merge", "reject", "split", "name", "deck_pilot", "deck_archetype"]

# The dictionary is checked in, and lives apart from `snapshots/` (immutable
# source) and `data/` (derived artifacts): it is neither, it is human judgement.
CURATION_PATH = Path("curation/pilots.toml")


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
class DeadEntry:
    """A recorded decision that matches no id or deck in the current snapshot.

    The dictionary is append-only and outlives the snapshots it was written
    against, so a typo'd id or an upstream-reissued pseudonym leaves an entry
    that silently fires nothing (ADR 0005). Surfaced in the reconciliation
    report -- never fatal -- so a maintainer can retire or repair it.
    """

    kind: DeadKind
    key: str  # the pilot id or deck id absent from the snapshot
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
    source title mislabelled its archetype. ``splits`` holds id pairs that share
    a display name but are different people, which keeps the identical-name join
    (ADR 0007) from folding them into one node -- the inverse of a ``merge``.
    """

    merges: dict[str, str]
    rejected: frozenset[frozenset[str]]
    names: dict[str, str]
    deck_pilots: dict[str, str]
    deck_archetypes: dict[str, ArchetypeOverride] = field(default_factory=dict)
    splits: frozenset[frozenset[str]] = field(default_factory=frozenset)

    @classmethod
    def empty(cls) -> "Curation":
        return cls({}, frozenset(), {}, {}, {}, frozenset())

    def canonical(self, pilot_id: str) -> str:
        """The id ``pilot_id`` was merged into, or itself."""
        return self.merges.get(pilot_id, pilot_id)

    def is_rejected(self, a: str, b: str) -> bool:
        """Whether these two ids were judged to be different people."""
        return frozenset({a, b}) in self.rejected

    def is_split(self, a: str, b: str) -> bool:
        """Whether these two same-named ids were declared different people."""
        return frozenset({a, b}) in self.splits


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
        rejected=_pairs(raw.get("reject", []), path, "reject"),
        names=names,
        deck_pilots=_deck_pilots(raw.get("deck_pilot", []), path),
        deck_archetypes=_deck_archetypes(raw.get("deck_archetype", []), path),
        splits=_pairs(raw.get("split", []), path, "split"),
    )


def dead_entries(
    curation: Curation, pilot_ids: set[str], deck_ids: set[str]
) -> list[DeadEntry]:
    """Recorded decisions that key on an id or deck absent from the snapshot.

    ``pilot_ids`` is every id resolution can key on: the raw upstream pilot ids
    *and* the canonical bucket ids they merge into, so a name pinned on a live
    canonical whose members carry all the decks is not misread as dead. Merge
    members are checked against the same set; an absent member fired nothing.
    ``deck_ids`` are post-dedup, so an override stranded on a deduped-away deck
    is caught too. Nothing here raises, since one stale entry among many must
    never break a rebuild. The result is sorted so the report diffs cleanly.
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
    pairs: set[frozenset[str]] = set()
    for entry in entries:
        ids = _ids(entry, path, kind)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                pairs.add(frozenset({a, b}))
    return frozenset(pairs)


def _ids(entry: dict, path: Path, kind: str) -> list[str]:
    ids = entry.get("ids")
    if not isinstance(ids, list) or len(ids) < 2:
        raise CurationError(f"{path}: a [[{kind}]] entry needs `ids` of two or more")
    return ids


def _names(entries: list[dict], path: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    for entry in entries:
        pilot, display = entry.get("pilot"), entry.get("display_name")
        if not pilot or not display:
            raise CurationError(f"{path}: a [[name]] entry needs `pilot` and `display_name`")
        names[pilot] = display
    return names


def _deck_pilots(entries: list[dict], path: Path) -> dict[str, str]:
    decks: dict[str, str] = {}
    for entry in entries:
        deck, pilot = entry.get("deck"), entry.get("pilot")
        if not deck or not pilot:
            raise CurationError(f"{path}: a [[deck_pilot]] entry needs `deck` and `pilot`")
        decks[deck] = pilot
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
