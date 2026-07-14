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

# A deck title reads "<placement> <name> - <deck> - <event>". The name is what
# is left after dropping the leading placement token and taking the segment
# before the deck separator.

# A placement token is a run of digits or placeholders (``?``, ``X`` for an
# unknown placement) with an optional ordinal suffix (``st``/``nd``/``rd``/``th``,
# sometimes mistyped with a trailing ``h`` or missing letters), optionally a
# ``/`` or ``-`` range, e.g. ``05th/08th``, ``??st``, ``05th-8th``, ``42ndh``,
# ``19h``, ``XXth``.
_PLACEMENT = re.compile(
    r"^\s*-?\s*[\dxX?]+(?:st|nd|rd|th)?h?(?:\s*[/-]\s*[\dxX?]+(?:st|nd|rd|th)?h?)?\s+",
    re.IGNORECASE,
)

# The name/deck separator: a hyphen or en/em dash with whitespace on at least
# one side (" - ", "- ", " –"). Requiring a space keeps an intra-name hyphen
# ("John-Paul", "Chris K-H"), which has no surrounding spaces, from splitting.
_SEPARATOR = re.compile(r"\s+[-–—]\s*|\s*[-–—]\s+")


def display_name_from_title(title: str | None) -> str | None:
    """Recover the pilot's display name from a deck title, or ``None``.

    Strips a leading placement token and returns the segment before the deck
    separator. Best-effort: residual noise is expected to lose the majority
    vote in :func:`resolve_pilots`.
    """
    if title is None:
        return None
    stripped = _PLACEMENT.sub("", title, count=1)
    # A placement token is sometimes followed by its own separator
    # ("05th-8th - Kyle G - ..."), so take the first non-empty segment.
    for segment in _SEPARATOR.split(stripped):
        if segment.strip():
            return segment.strip()
    return None


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
    """One display name shared by several pilot ids: maybe one split person."""

    display_name: str
    pilots: list[str]


@dataclass(frozen=True)
class Reconciliation:
    variant_clusters: list[VariantCluster]
    under_merges: list[UnderMerge]
    null_pilots: list[ResolvedPilot]  # the re-keyed null bucket (ADR 0004)


@dataclass(frozen=True)
class PilotResolution:
    deck_pilot: dict[str, str]  # deckId -> resolved pilot key
    pilots: list[ResolvedPilot]
    report: Reconciliation


def resolve_pilots(decks) -> PilotResolution:
    """Resolve decks to keyed, named pilots and a reconciliation report.

    Real pilots keep their upstream id and take a majority display name (with
    fuzzy-consolidated spelling variants). Null-pilot decks are re-keyed as
    low-confidence per-name pilots. The report surfaces the cases the data
    cannot resolve on its own: variant clusters that were merged, display names
    shared across real ids, and the re-keyed null bucket.
    """
    deck_pilot: dict[str, str] = {}
    real_pilots: list[ResolvedPilot] = []
    null_pilots: list[ResolvedPilot] = []
    variant_clusters: list[VariantCluster] = []

    real: dict[str, list] = {}
    null: dict[str, list] = {}
    for deck in decks:
        bucket = null if deck.pilot in NULL_PILOT_IDS else real
        bucket.setdefault(deck.pilot, []).append(deck)

    for pilot_id, group in real.items():
        names = [display_name_from_title(d.name) for d in group]
        display, merged = _choose_display_name(names, fallback=pilot_id)
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
        name = display_name_from_title(deck.name)
        key = f"nan:{name.casefold()}" if name else f"nan:deck:{deck.deck_id}"
        _, group = null_groups.setdefault(key, (name or "unknown", []))
        group.append(deck)
    for key, (display, group) in null_groups.items():
        null_pilots.append(ResolvedPilot(key, display, low_confidence=True))
        for d in group:
            deck_pilot[d.deck_id] = key

    # Under-merges are scanned over real pilots only: the null bucket is already
    # surfaced separately, so including it would just double-report the noise.
    report = Reconciliation(variant_clusters, _under_merges(real_pilots), null_pilots)
    return PilotResolution(
        deck_pilot=deck_pilot,
        pilots=real_pilots + null_pilots,
        report=report,
    )


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


def _under_merges(pilots: list[ResolvedPilot]) -> list[UnderMerge]:
    """Display names claimed by two or more distinct pilot ids."""
    by_name: dict[str, tuple[str, list[str]]] = {}
    for p in pilots:
        display, ids = by_name.setdefault(p.display_name.casefold(), (p.display_name, []))
        ids.append(p.pilot)
    return [
        UnderMerge(display_name=display, pilots=ids)
        for display, ids in by_name.values()
        if len(ids) > 1
    ]


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
