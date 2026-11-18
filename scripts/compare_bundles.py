"""Freeze what the site serves today, and compare a later build against it (issue #380).

A change that refiles decks under other archetypes (#381 files every deck under its
archetype family) moves nearly every archetype figure on the site, and what has to
be read before it ships is which of those moves it meant. So this takes every answer
the site serves that an archetype can move, and the ones no archetype may move, off
one bundle (the *capture*), and compares two captures of the same record answer by
answer. Decks keep their ids, so each deck's primary archetype in both builds says
which archetypes gained or lost decks, and every difference is sorted:

- **explained**: it involves an archetype that gained or lost decks, in the year,
  event or pilot the answer is about where it is about one;
- **allowed with a reason**: an answer about an archetype that did not move, which by
  construction reads the ones that did (a rank, an interval pooled over the year, a
  gem pushed off a node budget), each checked against the move that justifies it;
- **regression**: anything else, pilot identity, the record's totals and the player
  leaderboard included.

It also checks conservation: each archetype's decks after are the decks, before, of
the archetypes now filed under it.

``freeze`` copies a bundle aside with its capture, the commit it was built at and
the snapshots it was built from, so the reference is the site as main served it, read
by main's own code. ``compare`` captures a later bundle with the working tree's code
and prints the report, failing on any regression or conservation break. A local
tool, not part of the build or the app.

Usage (from the repo root, with graphs built from the snapshots)::

    uv run python scripts/compare_bundles.py freeze                 # data/graph -> data/reference
    uv run python scripts/compare_bundles.py compare --db data/graph
"""

import argparse
import json
import os
import shutil
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import ladybug

# How far a float may move between two builds and still be the same answer: the
# baseline gate's own, for the same reason (aggregation order redraws the last bits
# of a mean between builds).
from graph7ph.baseline import TOLERANCE
from graph7ph.build import graph_counts, reconciliation_path
from graph7ph.curation_report import latest_snapshot, unreportable
from graph7ph.db import artifact_path, database_path, open_database, rows
from graph7ph.figures import figures_path
from graph7ph.ingest import _snapshot_dirs
from graph7ph.query import (
    _fit_to_budget,
    _screen_gems,
    card_catalogue,
    card_usage_subgraph,
    gem_archetypes,
    hidden_gems_subgraph,
    pilot_affinity_subgraph,
    pilot_catalogue,
    pilot_record,
)
from graph7ph.trends import (
    NotEnoughHistory,
    _cut_only_events,
    _within_archetype_sd,
    archetype_events_played,
    archetype_landscape,
    archetype_timeline,
    archetypes_with_history,
    meta_share_over_time,
    player_leaderboard,
)

EXPLAINED, ALLOWED, REGRESSION = "explained", "allowed", "regression"

# The board filters the Cards tab offers on card usage: either board, or one.
BOARDS = {"any": None, "Main": "Main", "Side": "Side"}

# The reconciliation report's sections that say who is who, the identity rules'
# stranger base rate included: it is measured over archetype-and-colour pools, so it
# moves when a change reaches identity's inputs even where no pair changes state.
# The rest of the report is about the source's records (repairs, imputed values,
# field evidence), which the record and figure sections grade by what they serve.
IDENTITY = (
    "variant_clusters", "null_pilots", "event_splits", "dropped_duplicates",
    "joined_names", "curated", "multi_name_ids", "name_splits", "unexplained_names",
    "fired_holds", "held_names", "refused_entries", "ruled_pairs", "rule_overrides",
    "stranger_base_rate", "dead_entries",
)

# The dictionary entries about who is who, as a dead entry names its kind. A dead
# archetype ruling (one upstream has moved under) is an archetype answer, not identity.
IDENTITY_RULINGS = {"merge", "reject", "split", "name", "deck_pilot", "hold"}


class _Absent:
    """The side of a difference that holds no such entry at all."""

    def __repr__(self) -> str:
        return "absent"


ABSENT = _Absent()


@dataclass(frozen=True)
class Difference:
    """One answer that differs: ``field`` is ``None`` where the whole entry differs,
    and ``before`` or ``after`` is :data:`ABSENT` where it is on record in one build
    only. ``verdict`` and ``reason`` are the comparison's ruling."""

    section: str
    key: tuple
    field: str | None
    before: object
    after: object
    verdict: str = ""
    reason: str = ""


@dataclass(frozen=True)
class Break:
    """An archetype whose ``decks`` after are not the decks the archetypes now filed
    under it (``filed``) ``held`` before: a deck appeared, vanished, or came from an
    archetype whose other decks went elsewhere."""

    archetype: str | None
    decks: int
    filed: tuple[str | None, ...]
    held: int


@dataclass
class Comparison:
    """``moved`` is every archetype whose decks changed, as its deck count before and
    after. ``transitions`` counts the decks that changed archetype, by the pair they
    moved between. ``conservation`` is every archetype that breaks conservation.
    ``names`` is each archetype's display name, by code."""

    differences: list[Difference]
    moved: dict[str, tuple[int, int]]
    transitions: dict[tuple[str | None, str | None], int]
    conservation: list[Break]
    names: dict[str | None, str]


# --- Capture -------------------------------------------------------------------------


def _key(*parts) -> str:
    """A section's key as text, since JSON keys are strings: the parts as a JSON list."""
    return json.dumps(parts)


def _plain(value):
    """A value as JSON holds it: a date as ISO text, a tuple as a list."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _row(cell, *drop: str) -> dict:
    return {k: _plain(v) for k, v in asdict(cell).items() if k not in drop}


def _flatten(value, path=()) -> dict:
    """A JSON document as one entry per leaf, keyed by the path to it."""
    if isinstance(value, dict):
        return {k: v for name, item in value.items()
                for k, v in _flatten(item, path + (name,)).items()}
    if isinstance(value, list):
        return {k: v for i, item in enumerate(value)
                for k, v in _flatten(item, path + (str(i),)).items()}
    return {_key(*path): value}


def _archetype_sections(conn) -> dict:
    """Every archetype's share and count by year (its share over time), its finish on
    each year's landscape, its rank and events played in the latest year (the year an
    archetype's page states them for), and the year totals they are read against."""
    shares = meta_share_over_time(conn).cells
    years = sorted({cell.year for cell in shares})
    skip = _cut_only_events(conn)
    out = {name: {} for name in (
        "years", "meta_share", "ranks", "landscape", "events_played")}
    for year in years:
        cells = [cell for cell in shares if cell.year == year]
        played = [cell.n for cell in cells if cell.n]
        for cell in cells:
            out["meta_share"][_key(cell.tag, year)] = _row(
                cell, "tag", "year", "year_total")
            # The tile's own rule (app._archetype_tiles): one more than the archetypes
            # on more decks, and how many others are level with it.
            if cell.n and year == years[-1]:
                out["ranks"][_key(cell.tag, year)] = {
                    "rank": 1 + sum(n > cell.n for n in played),
                    "level": played.count(cell.n) - 1,
                }
        events, year_events = archetype_events_played(conn, year)
        if year == years[-1]:
            out["events_played"].update(
                {_key(tag, year): n for tag, n in events.items()})
        try:
            landscape = archetype_landscape(conn, year).cells
        except NotEnoughHistory:
            landscape = []
        for cell in landscape:
            out["landscape"][_key(cell.tag, year)] = _row(
                cell, "tag", "year", "year_total", "year_events")
        out["years"][_key(year)] = {
            "decks": sorted({cell.year_total for cell in cells}),
            "events": year_events,
            "archetypes": len(played),
            "spread": _within_archetype_sd(conn, year, skip),
        }
    return out


def _finishes(conn) -> dict:
    picker, finishes = {}, {}
    for name, tag, events in archetypes_with_history(conn):
        picker[_key(tag)] = {"name": name, "events": events}
        for point in archetype_timeline(conn, tag).cells:
            finishes[_key(tag, point.event)] = {
                "date": _plain(point.date), "mean_norm": point.mean_norm_a,
                "decks": point.decks_a, "bound": point.bound_a,
                "field_imputed": point.field_imputed,
            }
    return {"archetype_picker": picker, "finishes": finishes}


def _card_usage(db: ladybug.Database) -> dict:
    """Every card's usage by archetype under each board filter, as the Cards tab
    draws it: the card's own share, each broad class's, and each archetype's.

    The slow part of a capture, about 16,600 queries of which a board-filtered one
    takes 70 to 90 ms on the 2026-11-18 record (an unfiltered one 17 ms), so they
    run on threads sharing the read-only database, a connection each, as the app's
    requests do: measured 2.9 times faster on eight, with the same answers."""

    def usages(cards: list[str]) -> list:
        conn = ladybug.Connection(db)
        return [(canon, board, card_usage_subgraph(conn, canon, filtered))
                for canon in cards for board, filtered in BOARDS.items()]

    cards = [canon for _, canon in card_catalogue(ladybug.Connection(db))]
    workers = os.cpu_count() or 1
    with ThreadPoolExecutor(workers) as pool:
        drawn = [row for part in pool.map(usages, [cards[i::workers]
                                                    for i in range(workers)])
                 for row in part]
    out = {"card_usage_cards": {}, "card_usage_macros": {}, "card_usage": {}}
    for canon, board, usage in drawn:
        card = usage.nodes[0]
        out["card_usage_cards"][_key(canon, board)] = {
            "decks": card.decks, "total": card.total_decks}
        for edge in usage.edges:
            if edge.source == card.id:
                out["card_usage_macros"][_key(
                    canon, board, edge.target.removeprefix("macro:"))] = {
                    "decks": edge.decks, "total": edge.total_decks}
            else:
                out["card_usage"][_key(
                    canon, board, edge.target.removeprefix("arch:"))] = {
                    "macro": edge.source.removeprefix("macro:"),
                    "decks": edge.decks, "total": edge.total_decks}
    return out


def _gems(conn) -> dict:
    """The gem list, one entry per gem with the top-cut decks drawn under it, the
    list's own luck count, and whether the node budget cut it short."""
    gems = hidden_gems_subgraph(conn)
    nodes = {node.id: node for node in gems.nodes}
    listed: dict[str, dict] = {}
    for node in gems.nodes:
        if node.kind == "Card":
            _, tag, canon = node.id.split(":", 2)
            listed[node.id] = {
                "key": _key(tag, canon), "name": node.label, "decks": node.decks,
                "total": node.total_decks, "pilots": node.pilots,
                "top_decks": node.top_decks, "luck": node.gem_luck, "drawn": {}}
    for edge in gems.edges:
        if edge.source.startswith("deck:"):
            deck = nodes[edge.source]
            listed[edge.target]["drawn"][deck.id.removeprefix("deck:")] = [
                deck.label, deck.event, edge.label.removeprefix("CONTAINS:")]
    found, _ = _screen_gems(conn)
    return {
        "gems": {entry.pop("key"): entry for entry in listed.values()},
        "gem_list": {_key("expected_by_luck"): gems.expected_by_luck,
                     _key("budget_binds"): len(_fit_to_budget(found)) < len(found)},
        "gem_archetypes": {_key(tag): name for name, tag in gem_archetypes(conn)},
    }


def _pilots(conn) -> dict:
    """Every pilot's name, archetype affinity and record of decks."""
    out = {name: {} for name in (
        "pilots", "affinity", "affinity_macros", "affinity_edges", "records")}
    for name, pilot in pilot_catalogue(conn):
        out["pilots"][_key(pilot)] = name
        affinity = pilot_affinity_subgraph(conn, pilot)
        for edge in affinity.edges:
            if edge.source.startswith("pilot:"):
                out["affinity_macros"][_key(
                    pilot, edge.target.removeprefix("macro:"))] = edge.events
            else:
                macro = edge.source.removeprefix("macro:")
                tag = edge.target.removeprefix("arch:")
                out["affinity_edges"][_key(pilot, macro, tag)] = edge.events
        for node in affinity.nodes:
            if node.kind == "Archetype":
                out["affinity"][_key(pilot, node.id.removeprefix("arch:"))] = node.weight
        for deck in pilot_record(conn, pilot):
            out["records"][_key(pilot, deck.deck_id)] = _row(deck, "deck_id")
    return out


def _leaderboard(conn) -> dict:
    try:
        cells = player_leaderboard(conn).cells
    except NotEnoughHistory:
        return {}
    return {_key(cell.pilot, _plain(cell.as_of)): _row(cell, "pilot", "as_of")
            for cell in cells}


def _identity(recon: dict) -> dict:
    """Pilot identity as the reconciliation report states it: each ruled pair by its
    two pilots, every other list entry by its whole content."""
    out = {}
    for section in IDENTITY:
        value = recon.get(section)
        if section == "ruled_pairs":
            out.update({_key(section, *sorted(entry["pilots"])): entry for entry in value})
        elif section == "dead_entries":
            out.update({_key(section, json.dumps(entry, sort_keys=True)): True
                        for entry in value if entry["kind"] in IDENTITY_RULINGS})
        elif isinstance(value, list):
            out.update({_key(section, json.dumps(entry, sort_keys=True)): True
                        for entry in value})
        else:
            out[_key(section)] = value
    return out


def capture(artifact: Path) -> dict:
    """Everything the bundle at ``artifact`` serves that this comparison grades, by
    section, each entry keyed by what identifies it (a JSON list as text)."""
    artifact = Path(artifact)
    db = open_database(artifact, read_only=True)
    conn = ladybug.Connection(db)
    archetypes = {
        _key(tag): {"name": name, "decks": n}
        for tag, name, n in rows(conn.execute(
            """MATCH (d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype)
               RETURN a.tag, a.name, count(DISTINCT d)"""))
    }
    return {
        # Each deck's primary archetype, and where and by whom it was played, which
        # is what every verdict is read against.
        "decks": {
            _key(deck): {"archetype": tag, "year": year, "event": event, "pilot": pilot}
            for deck, tag, year, event, pilot in rows(conn.execute(
                """MATCH (p:Pilot)<-[:PILOTED_BY]-(d:Deck)-[:PLAYED_AT]->(e:Event)
                         -[:IN_YEAR]->(y:Year)
                   OPTIONAL MATCH (d)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype)
                   RETURN d.deckId, a.tag, y.year, e.event, p.pilot"""))
        },
        "counts": {_key(table): n for table, n in asdict(graph_counts(conn)).items()},
        "archetypes": archetypes,
        **_archetype_sections(conn),
        **_finishes(conn),
        **_card_usage(db),
        **_gems(conn),
        **_pilots(conn),
        "leaderboard": _leaderboard(conn),
        "figures": _flatten(json.loads(figures_path(artifact).read_text())),
        "identity": _identity(json.loads(reconciliation_path(artifact).read_text())),
    }


# --- Compare -------------------------------------------------------------------------


@dataclass(frozen=True)
class _Context:
    """What a rule reads besides the difference itself: each deck's primary archetype
    in both builds; which archetypes gained or lost decks, overall and within each
    year, event and pilot (``moved_by``, by scope and then by its value); each build's
    deck counts by year and archetype; the archetypes hung under each broad class of
    each card's usage in either build; the archetypes a gem can come from in either
    build; whether the gem budget cut either build's list; and both captures."""

    deck_archetype: tuple[dict[str, str | None], dict[str, str | None]]
    moved: frozenset[str | None]
    moved_by: dict[str, dict[object, frozenset[str | None]]]
    deck_counts: tuple[dict[int, dict[str, int]], dict[int, dict[str, int]]]
    under: dict[tuple, set[str]]
    gem_pool: frozenset[str]
    budget_binds: bool
    captures: tuple[dict, dict]


# Where and by whom a deck was played: the scopes an answer can be about.
SCOPES = ("year", "event", "pilot")


def _context(before: dict, after: dict) -> _Context:
    captures = (before, after)
    old = {raw: entry["archetype"] for raw, entry in before["decks"].items()}
    new = {raw: entry["archetype"] for raw, entry in after["decks"].items()}

    def changed(decks: set[str]) -> frozenset:
        held: dict[str | None, tuple[set, set]] = {}
        for raw in decks:
            if raw in old:
                held.setdefault(old[raw], (set(), set()))[0].add(raw)
            if raw in new:
                held.setdefault(new[raw], (set(), set()))[1].add(raw)
        return frozenset(tag for tag, (b, a) in held.items() if b != a)

    placed = {raw: entry for side in captures for raw, entry in side["decks"].items()}
    moved_by: dict[str, dict[object, frozenset]] = {}
    for scope in SCOPES:
        decks_at: dict[object, set[str]] = {}
        for raw, entry in placed.items():
            decks_at.setdefault(entry[scope], set()).add(raw)
        moved_by[scope] = {where: changed(decks) for where, decks in decks_at.items()}

    def deck_counts(capture: dict) -> dict[int, dict[str, int]]:
        counted: dict[int, dict[str, int]] = {}
        for raw, cell in capture["meta_share"].items():
            tag, year = json.loads(raw)
            counted.setdefault(year, {})[tag] = cell["n"]
        return counted

    under: dict[tuple, set[str]] = {}
    for capture in captures:
        for raw, entry in capture["card_usage"].items():
            canon, board, tag = json.loads(raw)
            under.setdefault((canon, board, entry["macro"]), set()).add(tag)
    return _Context(
        deck_archetype=(old, new),
        moved=changed(set(placed)),
        moved_by=moved_by,
        deck_counts=(deck_counts(before), deck_counts(after)),
        under=under,
        gem_pool=frozenset(json.loads(raw)[0]
                           for capture in captures for raw in capture["gem_archetypes"]),
        budget_binds=any(c["gem_list"][_key("budget_binds")] for c in captures),
        captures=captures,
    )


def _conservation(old: dict, new: dict) -> list[Break]:
    counted_before, counted_after = Counter(old.values()), Counter(new.values())
    filed: dict[str | None, set] = {}
    for raw in old.keys() & new.keys():
        filed.setdefault(new[raw], set()).add(old[raw])
    breaks = []
    for tag in sorted(counted_after.keys() | filed.keys(), key=str):
        sources = tuple(sorted(filed.get(tag, set()), key=str))
        held = sum(counted_before[source] for source in sources)
        if held != counted_after[tag]:
            breaks.append(Break(tag, counted_after[tag], sources, held))
    return breaks


def _same(a: object, b: object) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= TOLERANCE
    return a == b


def _diff(section: str, before: dict, after: dict) -> list[Difference]:
    out = []
    for raw in sorted(before.keys() | after.keys()):
        key = tuple(json.loads(raw))
        if raw not in before or raw not in after:
            out.append(Difference(section, key, None, before.get(raw, ABSENT),
                                  after.get(raw, ABSENT)))
            continue
        b, a = before[raw], after[raw]
        if isinstance(b, dict) and isinstance(a, dict):
            out += [Difference(section, key, field, b.get(field), a.get(field))
                    for field in sorted(b.keys() | a.keys())
                    if not _same(b.get(field), a.get(field))]
        elif not _same(b, a):
            out.append(Difference(section, key, None, b, a))
    return out


def _moved_names(tags) -> str:
    return ", ".join(sorted(map(str, tags)))


def _regression(d: Difference, ctx: _Context) -> tuple[str, str]:
    return REGRESSION, "no archetype moves this answer"


def _of_archetype(position: int, scope: str | None = None, at: int | None = None):
    """The rule for an answer about one archetype, its code at ``position`` in the key:
    explained when that archetype gained or lost decks, a regression otherwise. An
    answer about one year, event or pilot (``scope``, its value at ``at`` in the key)
    is explained only by decks that moved there."""
    def rule(d: Difference, ctx: _Context) -> tuple[str, str]:
        tag, moved, where = d.key[position], ctx.moved, ""
        if scope:
            moved = ctx.moved_by[scope].get(d.key[at], frozenset())
            where = f" at {scope} {d.key[at]}"
        if tag in moved:
            return EXPLAINED, f"{tag} gained or lost decks{where}"
        return REGRESSION, f"{tag} gained and lost no decks{where}"
    return rule


_IN_ITS_YEAR = _of_archetype(0, "year", 1)


def _of_any_archetype(what: str):
    """The rule for an answer counted over every archetype: explained when any moved."""
    def rule(d: Difference, ctx: _Context) -> tuple[str, str]:
        if ctx.moved:
            return EXPLAINED, f"{what}, and {_moved_names(ctx.moved)} moved"
        return REGRESSION, "no archetype gained or lost decks"
    return rule


def _share_rule(d: Difference, ctx: _Context) -> tuple[str, str]:
    """The share matrix holds a row for every archetype across every year, zeros
    included, so a zero year on record in one build only comes and goes with the
    archetype itself, wherever its decks moved."""
    held = d.after if d.before is ABSENT else d.before if d.after is ABSENT else None
    if held is not None and held["n"] == 0 and d.key[0] in ctx.moved:
        return EXPLAINED, f"{d.key[0]} gained or lost decks, and its row spans every year"
    return _IN_ITS_YEAR(d, ctx)


def _deck_rule(d: Difference, ctx: _Context) -> tuple[str, str]:
    if d.field == "archetype":
        return EXPLAINED, f"the deck moved from {d.before} to {d.after}"
    if d.field is None:
        return REGRESSION, "a deck on record in one build only"
    return REGRESSION, f"a deck's {d.field} changed"


def _counts_rule(d: Difference, ctx: _Context) -> tuple[str, str]:
    if d.key[0] in ("archetypes", "has_archetype"):
        return _of_any_archetype("a count of archetypes or their tags")(d, ctx)
    return REGRESSION, f"the graph's {d.key[0]} count changed"


def _years_rule(d: Difference, ctx: _Context) -> tuple[str, str]:
    year = d.key[0]
    if d.field in ("archetypes", "spread") and (moved := ctx.moved_by["year"].get(year)):
        return EXPLAINED, (f"read over every archetype played in {year}, and "
                           f"{_moved_names(moved)} moved that year")
    return REGRESSION, f"the year's {d.field or 'record'} changed"


def _archetypes_rule(d: Difference, ctx: _Context) -> tuple[str, str]:
    if d.field == "name":
        return REGRESSION, "an archetype on record in both builds was renamed"
    return _of_archetype(0)(d, ctx)


def _rank_rule(d: Difference, ctx: _Context) -> tuple[str, str]:
    """A rank is one more than the archetypes on more decks, so it moves with every
    archetype that crosses it: allowed where each that crossed gained or lost decks
    that year."""
    verdict = _IN_ITS_YEAR(d, ctx)
    if verdict[0] == EXPLAINED:
        return verdict
    tag, year = d.key

    def standing(counts: dict[int, dict[str, int]]) -> dict[str, int]:
        played = counts.get(year, {})
        own = played.get(tag, 0)
        return {other: (n > own) - (n < own)
                for other, n in played.items() if n and other != tag}

    before, after = (standing(counts) for counts in ctx.deck_counts)
    crossed = {other for other in before.keys() | after.keys()
               if before.get(other, -1) != after.get(other, -1)}
    if crossed and crossed <= ctx.moved_by["year"].get(year, frozenset()):
        return ALLOWED, f"ranked against {_moved_names(crossed)}, which gained or lost decks"
    return REGRESSION, f"{tag} gained and lost no decks and nothing that moved crossed it"


def _landscape_rule(d: Difference, ctx: _Context) -> tuple[str, str]:
    """A landscape interval is the archetype's own mean and events over the year's
    spread, pooled over every archetype played: allowed to move with that spread alone."""
    verdict = _IN_ITS_YEAR(d, ctx)
    if verdict[0] == EXPLAINED or d.field not in ("mean_low", "mean_high"):
        return verdict
    tag, year = d.key
    cells = [c["landscape"][_key(tag, year)] for c in ctx.captures]
    spreads = [c["years"][_key(year)]["spread"] for c in ctx.captures]
    own_unchanged = all(_same(cells[0][f], cells[1][f]) for f in ("mean_norm", "events"))
    moved = ctx.moved_by["year"].get(year)
    if own_unchanged and not _same(*spreads) and moved:
        return ALLOWED, (f"its interval is drawn over {year}'s spread, pooled over every "
                         f"archetype, and {_moved_names(moved)} moved that year")
    return verdict


def _card_macro_rule(d: Difference, ctx: _Context) -> tuple[str, str]:
    """A broad class is drawn on a card's usage only above the archetypes hung under
    it, so it may come and go with them; its own numbers never move with archetypes."""
    if d.field is None:
        if moved := ctx.under.get(d.key, set()) & ctx.moved:
            return EXPLAINED, f"drawn above {_moved_names(moved)}, which moved"
    return REGRESSION, "a broad class's own usage does not move with archetypes"


def _gem_rule(d: Difference, ctx: _Context) -> tuple[str, str]:
    tag = d.key[0]
    if tag in ctx.moved:
        return EXPLAINED, f"{tag} gained or lost decks"
    if d.field is None and ctx.budget_binds:
        return ALLOWED, "gems compete for a fixed node budget, which cut the list"
    return REGRESSION, f"{tag} gained and lost no decks"


def _gem_list_rule(d: Difference, ctx: _Context) -> tuple[str, str]:
    """The luck count sums over every card screened in every archetype a gem can come
    from, so it moves when any of them gained or lost decks."""
    if moved := ctx.gem_pool & ctx.moved:
        return EXPLAINED, f"screened over {_moved_names(moved)}, which moved"
    return REGRESSION, "no archetype a gem can come from gained or lost decks"


def _record_rule(d: Difference, ctx: _Context) -> tuple[str, str]:
    deck = d.key[1]
    old, new = (side.get(_key(deck)) for side in ctx.deck_archetype)
    if d.field == "archetype" and old != new:
        return EXPLAINED, f"the deck moved from {old} to {new}"
    return REGRESSION, "a pilot's record changed other than a deck's archetype"


# The figures file's archetype figures: the Golgari example is one archetype's, the
# gem list's are its luck rule's, and the rest are counted over every archetype.
_GOLGARI = {("golgari_tagged",), ("golgari_primary",)}
_GEM_FIGURES = {("gems_listed",), ("gems_expected_by_luck",)}
_ARCHETYPE_FIGURES = {
    ("decks_multi_archetype",), ("archetypes_offered",), ("archetypes_coin_close",),
    ("archetype_pairs_drawn",), ("archetype_pairs_bounded",),
    ("community", "totals", "archetypes"), ("community", "this_year", "archetypes"),
}


def _figures_rule(d: Difference, ctx: _Context) -> tuple[str, str]:
    if d.key in _GOLGARI:
        return _of_archetype(0)(replace(d, key=("golgari",)), ctx)
    if d.key in _GEM_FIGURES:
        return _gem_list_rule(d, ctx)
    if d.key in _ARCHETYPE_FIGURES:
        return _of_any_archetype("a figure counted over every archetype")(d, ctx)
    return REGRESSION, "a figure no archetype moves"


# Every section a capture holds: what the report calls it, in the order its detail
# is listed, and how its differences are ruled on (a function of the difference and
# the context, returning the verdict and its reason).
SECTIONS = {
    "decks": ("Each deck's archetype", _deck_rule),
    "counts": ("Table counts", _counts_rule),
    "years": ("Year totals", _years_rule),
    "archetypes": ("Archetypes on record", _archetypes_rule),
    "meta_share": ("Share by year", _share_rule),
    "ranks": ("Rank in the latest year", _rank_rule),
    "landscape": ("Metagame landscape", _landscape_rule),
    "events_played": ("Events played in the latest year", _IN_ITS_YEAR),
    "archetype_picker": ("Archetypes with a finishes line", _of_archetype(0)),
    "finishes": ("Finishes over time", _of_archetype(0, "event", 1)),
    "card_usage": ("Card usage by archetype", _of_archetype(2)),
    "card_usage_macros": ("Card usage by broad class", _card_macro_rule),
    "card_usage_cards": ("Card usage overall", _regression),
    "gems": ("Hidden gems", _gem_rule),
    "gem_list": ("Hidden gem list", _gem_list_rule),
    "gem_archetypes": ("Archetypes a gem can come from", _of_archetype(0)),
    "pilots": ("Pilot names", _regression),
    "affinity": ("Pilot archetype affinity", _of_archetype(1, "pilot", 0)),
    "affinity_macros": ("Pilot broad-class affinity", _regression),
    "affinity_edges": ("Pilot affinity by class and archetype",
                       _of_archetype(2, "pilot", 0)),
    "records": ("Pilot deck records", _record_rule),
    "leaderboard": ("Player leaderboard", _regression),
    "figures": ("Figures file", _figures_rule),
    "identity": ("Pilot identity", _regression),
}


def compare(before: dict, after: dict) -> Comparison:
    """Every way ``after`` differs from ``before``, each with its verdict."""
    ctx = _context(before, after)
    differences = []
    for section in sorted(before.keys() | after.keys()):
        _, rule = SECTIONS.get(section, (section, _regression))
        differences += [
            replace(d, verdict=verdict, reason=reason)
            for d in _diff(section, before.get(section, {}), after.get(section, {}))
            for verdict, reason in [rule(d, ctx)]
        ]
    old, new = ctx.deck_archetype
    counted_before, counted_after = Counter(old.values()), Counter(new.values())
    return Comparison(
        differences,
        moved={tag: (counted_before[tag], counted_after[tag]) for tag in ctx.moved},
        transitions=dict(Counter((old[raw], new[raw]) for raw in old.keys() & new.keys()
                                 if old[raw] != new[raw])),
        conservation=_conservation(old, new),
        names={None: "no archetype"} | {
            json.loads(raw)[0]: entry["name"]
            for capture in (before, after) for raw, entry in capture["archetypes"].items()},
    )


# --- Report --------------------------------------------------------------------------


def _counted(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


def _line(d: Difference) -> str:
    label, _ = SECTIONS.get(d.section, (d.section, None))
    where = ", ".join([label, " ".join(map(str, d.key))] + ([d.field] if d.field else []))
    if d.after is ABSENT:
        change = f"only in the reference, {d.before!r}"
    elif d.before is ABSENT:
        change = f"only in this build, {d.after!r}"
    else:
        change = f"{d.before!r} became {d.after!r}"
    return f"  {where}: {change} ({d.reason})"


def _moved_lines(result: Comparison) -> list[str]:
    name = result.names.get
    lines = []
    for tag, (b, a) in sorted(result.moved.items(), key=lambda kv: str(name(kv[0], kv[0]))):
        came = [f"{n} from {name(src, src)}" for (src, dst), n in
                sorted(result.transitions.items(), key=str) if dst == tag]
        went = [f"{n} to {name(dst, dst)}" for (src, dst), n in
                sorted(result.transitions.items(), key=str) if src == tag]
        why = f" ({'; '.join(came + went)})" if came or went else ""
        lines.append(f"  {name(tag, tag)}: {_counted(b, 'deck')} before, {a} after{why}")
    return lines


def _break_line(b: Break, names: dict) -> str:
    filed = ", ".join(sorted(str(names.get(t, t)) for t in b.filed)) or "none"
    return (f"  {names.get(b.archetype, b.archetype)} has {_counted(b.decks, 'deck')}, "
            f"but the archetypes now filed under it ({filed}) held {b.held}")


def report(result: Comparison) -> str:
    """The comparison for a person: how many differences of each kind and which
    archetypes moved, then every regression and every allowance in full, then the
    explained differences counted by answer."""
    by = {v: [d for d in result.differences if d.verdict == v]
          for v in (EXPLAINED, ALLOWED, REGRESSION)}
    lines = [
        f"{_counted(len(result.differences), 'difference')}: "
        f"{len(by[EXPLAINED])} explained, {len(by[ALLOWED])} allowed with a reason, "
        f"{_counted(len(by[REGRESSION]), 'regression')}."
        if result.differences else
        "No differences: the build serves exactly what the reference does."
    ]
    if result.conservation:
        lines.append(f"Decks are not conserved for "
                     f"{_counted(len(result.conservation), 'archetype')}:")
        lines += [_break_line(b, result.names) for b in result.conservation]
    else:
        lines.append(
            "Every archetype holds the decks of the archetypes now filed under it.")
    if result.moved:
        lines.append(f"{_counted(len(result.moved), 'archetype')} gained or lost decks:")
        lines += _moved_lines(result)
    else:
        lines.append("No archetype gained or lost decks.")
    lines += ["", f"Regressions ({len(by[REGRESSION])}):"]
    lines += [_line(d) for d in by[REGRESSION]]
    lines += ["", f"Allowed with a reason ({len(by[ALLOWED])}):"]
    lines += [_line(d) for d in by[ALLOWED]]
    lines += ["", f"Explained ({len(by[EXPLAINED])}), by answer:"]
    counted = Counter(d.section for d in by[EXPLAINED])
    lines += [f"  {label}: {counted[section]}"
              for section, (label, _) in SECTIONS.items() if counted[section]]
    return "\n".join(lines) + "\n"


# --- Freeze and compare --------------------------------------------------------------

REFERENCE = Path("data/reference")
SNAPSHOTS = Path("snapshots")


def _snapshot_set(root: Path) -> list[str]:
    """The snapshots a build over ``root`` reads, by name, oldest first."""
    return [path.name for path in _snapshot_dirs(root)]


def _usable(artifact: Path, snapshots: Path) -> None:
    """Refuse a bundle that is not today's code over the newest snapshot."""
    if not database_path(artifact).exists():
        raise SystemExit(f"No graph at {artifact}: build one first.")
    newest = latest_snapshot(snapshots)
    if newest is None:
        raise SystemExit(f"No snapshots in {snapshots}/.")
    if (complaint := unreportable(artifact, newest)) is not None:
        raise SystemExit(f"Cannot capture: {complaint}.")


def freeze(artifact: Path, target: Path, snapshots: Path, commit: str) -> None:
    """Keep the bundle at ``artifact`` aside at ``target`` as the reference: a copy of
    it, its capture, and the commit and snapshots it was built from. Never over an
    existing reference."""
    artifact, target = Path(artifact), Path(target)
    if target.exists():
        raise SystemExit(f"{target} already holds a reference: remove it by hand to "
                         "freeze another.")
    _usable(artifact, snapshots)
    shutil.copytree(artifact, target / "graph")
    (target / "capture.json").write_text(json.dumps(capture(target / "graph")))
    built = json.loads((artifact / "provenance.json").read_text())
    # Written last, so a freeze that stopped partway leaves no reference to compare to.
    (target / "reference.json").write_text(json.dumps({
        "commit": commit,
        "snapshots": _snapshot_set(snapshots),
        "source_digest": built["source_digest"],
        "built_at": built["built_at"],
        "frozen_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2) + "\n")


def compare_to_reference(reference: Path, artifact: Path, snapshots: Path) -> Comparison:
    """The bundle at ``artifact`` compared against the reference at ``reference``,
    refused unless both were built from the same snapshots."""
    reference = Path(reference)
    if not (reference / "reference.json").exists():
        raise SystemExit(f"No reference at {reference}: freeze one first.")
    recorded = json.loads((reference / "reference.json").read_text())
    on_disk = _snapshot_set(snapshots)
    if on_disk != recorded["snapshots"]:
        added = sorted(set(on_disk) - set(recorded["snapshots"]))
        gone = sorted(set(recorded["snapshots"]) - set(on_disk))
        raise SystemExit(
            f"The snapshots in {snapshots}/ are not the ones the reference was built "
            f"from (new: {', '.join(added) or 'none'}; missing: {', '.join(gone) or 'none'})"
            ": the two builds would hold different records.")
    _usable(Path(artifact), snapshots)
    before = json.loads((reference / "capture.json").read_text())
    return compare(before, capture(Path(artifact)))


def _commit() -> str:
    """The commit the working tree is at, refused while a build input is edited, since
    the commit would then not be the code that built the bundle."""
    edited = subprocess.run(
        ["git", "status", "--porcelain", "--", "src/graph7ph", "curation"],
        capture_output=True, text=True, check=True).stdout
    if edited:
        raise SystemExit("Build inputs have uncommitted changes, so no commit names the "
                         f"code that built the bundle:\n{edited}")
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="compare_bundles.py")
    sub = parser.add_subparsers(required=True, dest="command")
    p_freeze = sub.add_parser("freeze", help="Keep a bundle aside as the reference")
    p_freeze.add_argument("--db", type=Path, default=artifact_path())
    p_freeze.add_argument("--to", type=Path, default=REFERENCE)
    p_freeze.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    p_compare = sub.add_parser("compare", help="Compare a bundle against the reference")
    p_compare.add_argument("--reference", type=Path, default=REFERENCE)
    p_compare.add_argument("--db", type=Path, default=artifact_path())
    p_compare.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    args = parser.parse_args(argv)

    if args.command == "freeze":
        freeze(args.db, args.to, args.snapshots, _commit())
        print(f"Froze {args.db} at {args.to}.")
        return
    result = compare_to_reference(args.reference, args.db, args.snapshots)
    recorded = json.loads((args.reference / "reference.json").read_text())
    print(report(result), end="")
    print(f"\nReference: {args.reference}, frozen from commit {recorded['commit']} over "
          f"{_counted(len(recorded['snapshots']), 'snapshot')} "
          f"({recorded['snapshots'][0]} to {recorded['snapshots'][-1]}).")
    if result.conservation or any(d.verdict == REGRESSION for d in result.differences):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
