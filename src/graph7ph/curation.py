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
from dataclasses import dataclass
from pathlib import Path

# The dictionary is checked in, and lives apart from `snapshots/` (immutable
# source) and `data/` (derived artifacts): it is neither, it is human judgement.
CURATION_PATH = Path("curation/pilots.toml")


class CurationError(Exception):
    """The dictionary is malformed, or contradicts itself."""


@dataclass(frozen=True)
class Curation:
    """Human decisions the build applies on every rebuild.

    ``merges`` maps an upstream pilot id onto the canonical id it belongs to,
    already flattened transitively, so ``Alex J -> Alexander J -> Alexadner J``
    resolves in one lookup. ``rejected`` holds id pairs judged *not* to be the
    same person, which suppresses them from the candidate report for good.
    ``names`` pins a display name against the majority vote. ``deck_pilots``
    reassigns one deck to an upstream pilot id, which is how a null-pilot deck
    reaches its real owner.
    """

    merges: dict[str, str]
    rejected: frozenset[frozenset[str]]
    names: dict[str, str]
    deck_pilots: dict[str, str]

    @classmethod
    def empty(cls) -> "Curation":
        return cls({}, frozenset(), {}, {})

    def canonical(self, pilot_id: str) -> str:
        """The id ``pilot_id`` was merged into, or itself."""
        return self.merges.get(pilot_id, pilot_id)

    def is_rejected(self, a: str, b: str) -> bool:
        """Whether these two ids were judged to be different people."""
        return frozenset({a, b}) in self.rejected


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

    return Curation(
        merges=_merges(raw.get("merge", []), path),
        rejected=frozenset(
            frozenset(_ids(entry, path, "reject")) for entry in raw.get("reject", [])
        ),
        names=_names(raw.get("name", []), path),
        deck_pilots=_deck_pilots(raw.get("deck_pilot", []), path),
    )


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
