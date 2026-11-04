"""Source repairs: rulings about upstream's data, applied as a snapshot is loaded.

The pilot dictionary (:mod:`graph7ph.curation`) records decisions about identity
and applies them on every build. This file records decisions about the *source's
own records* that the build cannot take on faith and the gate cannot see through:
a deck filed at an event it was not played at, a second event folded into a
first, a historical fact upstream rewrote and the owner adopted. Before this file
those rulings lived as edits to the gitignored snapshot files, so a fresh clone
could not build today's graph and every fetch re-tripped the guards until the
same edits were repeated by hand (issue #268).

Every entry keys on deck ids, the source's stable identifier, and is applied to
every snapshot that holds the deck, before the ingest gate or any build guard
reads it. The repair is not a layer over the graph: the snapshot the build sees
is the repaired one, as if upstream had filed the record right.
"""

import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from graph7ph.curation import SNAPSHOT_STAMP
from graph7ph.models import Deck, Snapshot

# Checked in beside the pilot dictionary: both are human judgement, neither is
# source data nor a derived artifact.
REPAIRS_PATH = Path("curation/repairs.toml")


class RepairError(Exception):
    """The repairs file is malformed, contradicts itself, or the data refuses it."""


@dataclass(frozen=True)
class HoldOut:
    """Decks kept out of the graph entirely, from every snapshot that holds them."""

    decks: tuple[str, ...]
    reason: str
    as_of: str  # the snapshot the ruling was written against


@dataclass(frozen=True)
class Refile:
    """Decks moved to an event the source does not have.

    The source filed them under another event (upstream folded a second event
    into a first, ADR 0025), so the event they were played at exists nowhere in
    the source: the entry supplies its type and, where the owner knows it, its
    field. A field stated here is a ruling and not the source's number, so it
    reaches the graph under this kind's rule name rather than as a count the
    source stood behind (ADR 0016). With no field the build's own field rules
    decide, as they do for any event whose size the source left blank.
    """

    decks: tuple[str, ...]
    reason: str
    as_of: str
    event: str
    event_type: str
    field: int | None


# The immutable facts an `[[adopt]]` can state, as the source spells them, with
# the type the value must carry and the model field it lands on. These are the
# scalar half of `ingest._deck_hash`'s projection: a rewrite of any of them flags
# the deck and pins it at the first-seen value, so adopting the rewrite is the
# one way a human's acceptance of it reaches the graph. The event is not among
# them, because a deck that belongs at an event the source has is a
# `[[deck_event]]` in the pilot dictionary and one it does not is a `[[refile]]`;
# nor is the decklist, which nothing has ever rewritten.
ADOPTABLE: dict[str, tuple[type, str]] = {
    "pilot": (str, "pilot"),
    "placement": (int, "placement"),
    "createdAt": (datetime, "created_at"),
}


@dataclass(frozen=True)
class Adopt:
    """A value the owner adopts for one of a deck's immutable facts.

    Upstream rewrote the fact in a later fetch and the owner judged the rewrite
    right (a pilot re-keyed to the person's real id). The gate would flag the
    rewrite and pin the old value for good; carrying the adopted value into every
    snapshot that holds the deck makes the sequence read one fact, so the gate
    stays quiet and the graph carries the value the owner chose.
    """

    decks: tuple[str, ...]
    reason: str
    as_of: str
    fact: str  # a key of ADOPTABLE
    value: object


@dataclass(frozen=True)
class Repairs:
    """Every ruling the file holds, by kind; an absent file is all three empty."""

    hold_outs: tuple[HoldOut, ...] = ()
    refiles: tuple[Refile, ...] = ()
    adopts: tuple[Adopt, ...] = ()


def load_repairs(path: Path = REPAIRS_PATH) -> Repairs:
    """Read the repairs file, or return an empty one if it does not exist yet."""
    path = Path(path)
    if not path.exists():
        return Repairs()
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise RepairError(f"{path} is not valid TOML: {exc}") from exc
    if unknown := set(raw) - set(KEYS):
        raise RepairError(
            f"{path} holds {sorted(unknown)}, which no kind of repair is called; the "
            f"kinds are {sorted(KEYS)}"
        )
    repairs = Repairs(
        hold_outs=tuple(
            HoldOut(**_common(entry, path, "hold_out")) for entry in raw.get("hold_out", [])
        ),
        refiles=tuple(_refile(entry, path) for entry in raw.get("refile", [])),
        adopts=tuple(_adopt(entry, path) for entry in raw.get("adopt", [])),
    )
    _refuse_contradictions(repairs, path)
    return repairs


def _refuse_contradictions(repairs: Repairs, path: Path) -> None:
    """Refuse a file that rules two ways about one deck or one event.

    A held-out deck is not in the graph, so a second ruling about it applies to
    nothing; a deck refiled twice has two destinations; a fact adopted twice has
    two values; two refiles that build one event must describe the same event. In
    each case file order would decide which ruling the graph carries and nothing
    would record that the other was discarded (the same failure the pilot
    dictionary refuses in `curation._names`), so the author is asked to keep one.
    """
    held = {d for entry in repairs.hold_outs for d in entry.decks}
    refiled: dict[str, str] = {}
    events: dict[str, tuple[str, int | None]] = {}
    for entry in repairs.refiles:
        for d in entry.decks:
            if d in held:
                raise RepairError(
                    f"{path}: deck {d!r} is held out and also refiled; a held-out deck "
                    f"is not in the graph"
                )
            if d in refiled:
                raise RepairError(
                    f"{path}: deck {d!r} is refiled twice, to {refiled[d]!r} and "
                    f"{entry.event!r}; keep the ruling that supersedes"
                )
            refiled[d] = entry.event
        described = (entry.event_type, entry.field)
        if events.setdefault(entry.event, described) != described:
            raise RepairError(
                f"{path}: two [[refile]] entries build {entry.event!r} as different "
                f"events, {events[entry.event]} and {described} (type, field); one "
                f"event has one type and one field"
            )
    adopted: set[tuple[str, str]] = set()
    for entry in repairs.adopts:
        for d in entry.decks:
            if d in held:
                raise RepairError(
                    f"{path}: deck {d!r} is held out and also adopts a value; a "
                    f"held-out deck is not in the graph"
                )
            if (d, entry.fact) in adopted:
                raise RepairError(
                    f"{path}: deck {d!r} adopts two values for {entry.fact}; keep the "
                    f"ruling that supersedes"
                )
            adopted.add((d, entry.fact))


def _adopt(entry: dict, path: Path) -> Adopt:
    fact, value = entry.get("fact"), entry.get("value")
    if fact not in ADOPTABLE:
        raise RepairError(
            f"{path}: [[adopt]] names the fact {fact!r}; the facts a ruling can adopt a "
            f"value for are {sorted(ADOPTABLE)}"
        )
    if type(value) is not ADOPTABLE[fact][0]:
        raise RepairError(
            f"{path}: [[adopt]] states {value!r} for {fact}, which takes a "
            f"{ADOPTABLE[fact][0].__name__}"
        )
    # TOML reads a datetime without an offset as a local one; the source stamps
    # every createdAt in UTC, and a naive value cannot be compared with those.
    if fact == "createdAt" and value.tzinfo is None:
        raise RepairError(
            f"{path}: [[adopt]] states {value.isoformat()} for createdAt without an "
            f"offset; write it as the source does, in UTC (\"...T11:11:05Z\")"
        )
    return Adopt(**_common(entry, path, "adopt"), fact=fact, value=value)


def _refile(entry: dict, path: Path) -> Refile:
    event, event_type = entry.get("event"), entry.get("event_type")
    field = entry.get("field")
    if not (isinstance(event, str) and event and isinstance(event_type, str) and event_type):
        raise RepairError(f"{path}: a [[refile]] entry needs `event` and `event_type`")
    # `type` rather than `isinstance`: a TOML `true` is a bool, and a bool is an int.
    if field is not None and (type(field) is not int or field < 1):
        raise RepairError(
            f"{path}: [[refile]] to {event!r} states a `field` of {field!r}; a field is a "
            f"positive count of entrants, or left out where the owner does not know it"
        )
    return Refile(
        **_common(entry, path, "refile"), event=event, event_type=event_type, field=field
    )


# The keys each kind of entry may carry. A key outside them is refused rather
# than ignored: a misspelt `field` would otherwise read as "left out" and hand
# the event to the build's field rules, and nothing would say the file was
# never read as written.
KEYS = {
    "hold_out": {"decks", "reason", "as_of"},
    "refile": {"decks", "reason", "as_of", "event", "event_type", "field"},
    "adopt": {"decks", "reason", "as_of", "fact", "value"},
}


def _common(entry: dict, path: Path, kind: str) -> dict:
    """The three fields every kind carries, as keyword arguments for its dataclass:
    the decks it keys on, why, and the snapshot the ruling was written against."""
    if unknown := set(entry) - KEYS[kind]:
        raise RepairError(
            f"{path}: a [[{kind}]] entry carries {sorted(unknown)}, which it has no use "
            f"for; its keys are {sorted(KEYS[kind])}"
        )
    decks, reason, as_of = entry.get("decks"), entry.get("reason"), entry.get("as_of")
    if (not isinstance(decks, list) or not decks or len(set(decks)) != len(decks)
            or not all(isinstance(d, str) and d for d in decks)):
        raise RepairError(
            f"{path}: a [[{kind}]] entry needs `decks`, one or more distinct deck ids"
        )
    if not isinstance(reason, str) or not reason:
        raise RepairError(f"{path}: a [[{kind}]] entry needs a one-line `reason`")
    if not isinstance(as_of, str) or not SNAPSHOT_STAMP.match(as_of):
        raise RepairError(
            f"{path}: [[{kind}]] is `as_of` {as_of!r}, which is not a snapshot stamp; "
            f"it names the snapshot the ruling was written against, quoted "
            f'("20261104T091012Z")'
        )
    return {"decks": tuple(decks), "reason": reason, "as_of": as_of}


# What the source holds under each kind of repair, as the source spells it: the
# keys `apply_repairs` records per matched deck and `repairs_report` compares
# between snapshots. An adopt's key is the fact it adopts.
HOLD_OUT_HELD = ("present", "event", "eventId")
REFILE_HELD = ("event", "eventId")


def apply_repairs(snapshot: Snapshot, repairs: Repairs) -> tuple[Snapshot, dict[str, dict]]:
    """The snapshot with every repair applied, and what the source held under each.

    The second value maps every deck id a repair matched to the source's own
    value for what the repair rewrote, as the source spells it and JSON-ready: a
    refile's `event` and `eventId`, an adopt's fact, a hold-out's `present` with
    the event the source files it at. It rides back because only the caller
    holding the whole sequence can grade a repair: one that matched nothing
    anywhere is a dead entry, and one whose source value changes between
    snapshots is a ruling upstream has moved under, which the gate cannot see
    because it compares repaired snapshots (issue #278).
    """
    held = {d for entry in repairs.hold_outs for d in entry.decks}
    refiled = {d: entry for entry in repairs.refiles for d in entry.decks}
    adopted: dict[str, dict[str, object]] = {}
    for entry in repairs.adopts:
        for d in entry.decks:
            adopted.setdefault(d, {})[ADOPTABLE[entry.fact][1]] = entry.value
    # The event a refile builds must be one the source does not have in any
    # snapshot, this one included even if nothing here is refiled: seating the
    # ruling's type and field beside the source's own decks would leave the
    # event described two ways in the union the build reads, and a deck that
    # belongs at an event the source has is the pilot dictionary's
    # `[[deck_event]]`.
    built = {entry.event for entry in repairs.refiles}
    for d in snapshot.decks:
        if d.deck_id not in refiled and d.event in built:
            raise RepairError(
                f"[[refile]] builds event {d.event!r}, but the source already files decks "
                f"there ({d.deck_id!r}); a deck that belongs at an event the source has is a "
                f"[[deck_event]] in the pilot dictionary"
            )
    source_held: dict[str, dict] = {}
    decks = []
    for d in snapshot.decks:
        if d.deck_id in held:
            source_held[d.deck_id] = {"present": True, "event": d.event, "eventId": d.event_id}
            continue
        if d.deck_id in adopted:
            source_held.setdefault(d.deck_id, {}).update(
                (fact, _json_ready(getattr(d, field)))
                for fact, (_, field) in ADOPTABLE.items() if field in adopted[d.deck_id]
            )
            d = d.model_copy(update=adopted[d.deck_id])
        if d.deck_id in refiled:
            source_held.setdefault(d.deck_id, {}).update(event=d.event, eventId=d.event_id)
            d = _refiled(d, refiled[d.deck_id])
        decks.append(d)
    return Snapshot(
        cards=snapshot.cards, decks=decks,
        containments=[c for c in snapshot.containments if c.deck_id not in held],
    ), source_held


def _json_ready(value: object) -> object:
    return value.isoformat() if isinstance(value, datetime) else value


def _refiled(deck: Deck, entry: Refile) -> Deck:
    # Every event-scoped value the source put on the deck was a claim about the
    # event it was wrongly filed at, so each goes: the id (the new event has none
    # in the source), the type and field (the ruling's), and the norm, which the
    # source computed against the wrong event's field. `build._mint_norms` scores
    # the deck against the field the build settles for the new event and keeps the
    # rule named here, the way a `[[deck_event]]` leaves "reassigned". The title's
    # trailing event segment follows the move, since it is the one place a reader
    # sees the event spelled out on the deck itself.
    suffix = f" - {deck.event}"
    name = deck.name[: -len(deck.event)] + entry.event if deck.name.endswith(suffix) else deck.name
    return deck.model_copy(update={
        "event": entry.event, "event_id": None, "event_type": entry.event_type,
        "event_size": entry.field, "name": name,
        "placement_norm": None, "norm_imputed": "refiled",
        "field_imputed": "refiled" if entry.field is not None else None,
    })


def repairs_report(
    repairs: Repairs, applied: dict[str, list[tuple[str, dict]]], snapshots: list[str]
) -> dict:
    """The reconciliation report's ``source_repairs`` section.

    ``applied`` maps each deck id a repair matched to the snapshots it matched
    in, oldest first, each with what the source held there under the repair
    (the second value of :func:`apply_repairs`); ``snapshots`` names the whole
    sequence in the same order. One row per deck rather than per entry, since
    an entry of 44 decks can be live for 43 of them: a deck no snapshot held is
    a dead ruling, reported the way a dead dictionary entry is
    (`curation.dead_entries`) and never fatal, so a maintainer can retire or
    repair it without a stale id stopping the build.

    Dead means a mistyped or reissued id, not a ruling upstream has overtaken:
    the sequence is append-only and a deck keeps its id across an upstream
    correction, so an entry that was ever live stays live and keeps applying.
    ``moved`` is where upstream overtaking a ruling shows (issue #278): a repair
    is applied to every snapshot before the gate compares them, so the gate
    sees the repaired value on both sides and stays quiet when the source
    refiles a refiled deck to an event of its own, rewrites an adopted fact
    again, or drops or refiles a held-out deck. Each such deck is a row naming
    the two snapshots and what the source held on each; what to do about it
    (retire the entry, rename the event, carry upstream's field) is a ruling,
    so the build promotes and the row waits to be read. Only snapshots from
    the one the entry was written against count: the author ruled on
    everything the source had filed up to `as_of`, and in particular an adopt
    exists because the source rewrote the fact once, which is not the source
    moving again.
    """
    applied_rows, dead, moved = [], [], []
    for kind, entries, describe, held_keys in (
        ("hold_out", repairs.hold_outs, lambda e: "held out", lambda e: HOLD_OUT_HELD),
        ("refile", repairs.refiles, lambda e: f"refiled to {e.event}", lambda e: REFILE_HELD),
        ("adopt", repairs.adopts, lambda e: f"adopts {e.fact} = {e.value!r}",
         lambda e: (e.fact,)),
    ):
        for entry in entries:
            detail = f"{describe(entry)}: {entry.reason}"
            for deck in entry.decks:
                row = {"kind": kind, "deck": deck, "detail": detail}
                if deck not in applied:
                    dead.append(row)
                    continue
                applied_rows.append({**row, "snapshots": [name for name, _ in applied[deck]]})
                # Snapshot names are UTC stamps, so string order is time order.
                seen = [(name, {k: held[k] for k in held_keys(entry)})
                        for name, held in applied[deck] if name >= entry.as_of]
                if kind == "hold_out" and seen and seen[-1][0] != snapshots[-1]:
                    gone = snapshots[snapshots.index(seen[-1][0]) + 1]
                    seen.append((gone, {"present": False}))
                moved.extend(
                    {**row, "from": {"snapshot": before[0], **before[1]},
                     "to": {"snapshot": after[0], **after[1]}}
                    for before, after in zip(seen, seen[1:]) if before[1] != after[1]
                )
    return {"applied": applied_rows, "dead": dead, "moved": moved}
