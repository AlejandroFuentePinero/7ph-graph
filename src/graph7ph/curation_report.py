"""The post-ingestion curation brief: what this ingestion put on the table.

A review round used to start from `reconciliation.json`, which re-derives the
same flat queue every build and says nothing about what moved (issue #227). This
renders the same facts as a brief ranked on what changed, so a round starts from
a written page: decisions the data has settled first, then what is genuinely new,
then holds whose evidence moved, then the unchanged tail as one line each.

Every figure here is derived from the report the build just wrote and from the
dictionary, never transcribed, so nothing in the brief can go stale the way a
checked-in count would (issue #227).
"""

from datetime import datetime, timezone
from pathlib import Path

from graph7ph.curation import Hold
from graph7ph.pilots import SETTLING_TRIGGERS
from graph7ph.provenance import built_at, staleness

Holds = dict[frozenset[str], Hold]

# How `fetch` names a snapshot directory.
STAMP = "%Y%m%dT%H%M%SZ"


def _fetched_at(snapshot: str) -> datetime | None:
    """The fetch time a snapshot name records, or ``None`` where it is not one.

    Grading by reading rather than by shape, because :data:`curation.SNAPSHOT_STAMP`
    admits dates that never happened (a 31st of February sorts above every real
    stamp) and this is the only reader that has to turn the name into a time.
    """
    try:
        return datetime.strptime(snapshot, STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def latest_snapshot(snapshots_root: Path) -> str | None:
    """The newest snapshot on disk, or ``None`` where there is none.

    Newest by name, which is how the build orders the sequence too: the name is
    the fetch time, so name order is time order. Only directories a fetch could
    have written are considered, since this stamp is read as a time below and a
    hand-made directory alongside them would otherwise raise into a pipe whose
    other end files whatever it is given as the review issue.
    """
    stamps = [p.name for p in Path(snapshots_root).glob("*")
              if p.is_dir() and _fetched_at(p.name) is not None]
    return max(stamps, default=None)


def unreportable(artifact: Path, snapshot: str) -> str | None:
    """Why the bundle at ``artifact`` cannot be reported against ``snapshot``.

    Prose in the shape :func:`provenance.staleness` returns, and for the same
    reason: the caller frames it. Two ways to be wrong rather than one. A bundle
    built from other sources describes a graph today's code would not produce
    (issue #55). A bundle older than the newest snapshot describes the corpus
    before the one it would be filed against, which is the fetch-without-build
    case: the brief becomes an issue a human curates from, so reporting on
    superseded data costs a round of curation rather than a re-run.
    """
    if (complaint := staleness(artifact)) is not None:
        return complaint
    stamp, when = _fetched_at(snapshot), built_at(artifact)
    if stamp is not None and when is not None and datetime.fromisoformat(when) < stamp:
        return (f"the bundle at {artifact} was built at {when}, before snapshot "
                f"{snapshot} was fetched: run `uv run graph7ph build` before "
                "reporting on it")
    return None


def curation_report(recon: dict, holds: Holds, snapshot: str) -> str:
    """The review brief for the bundle ``recon`` came from, as markdown."""
    fired = recon.get("fired_holds", [])
    settled = [f for f in fired if f["trigger"] in SETTLING_TRIGGERS]
    moved = [f for f in fired if f["trigger"] not in SETTLING_TRIGGERS]

    # Unexamined *is* new: both queues carry only what no pass has read, and the
    # invariant that nothing else sits there is held by the suite
    # (`test_build.py::test_nothing_in_either_identity_queue_is_left_unexamined`)
    # rather than assumed here, so a row reaching this section is a row this
    # ingestion introduced (issues #231, #232).
    new = recon.get("under_merges", []) + recon.get("multi_name_ids", [])
    named = {frozenset(h["pilots"]): h for h in recon.get("held_merges", [])} | {
        frozenset({h["pilot"]}): h for h in recon.get("held_names", [])}
    has_fired = {frozenset(f["pilots"]) for f in fired}
    unchanged = {ids: hold for ids, hold in sorted(holds.items(), key=lambda kv: sorted(kv[0]))
                 if ids not in has_fired}

    # A brief filed after every ingestion is read only if its arrival means
    # something, so a round with no work says that in a sentence rather than in
    # a page of empty headings. The held tail is not work and does not make a
    # round loud, but it is still listed: a hold nobody lists is one nobody
    # retires. With no tail either there is nothing to print but the sentence.
    if not (fired or new):
        quiet = (f"Nothing to review for snapshot `{snapshot}`: no hold settled, "
                 "no candidate is new, and no hold's evidence moved.")
        if not unchanged:
            return quiet + "\n"
        lines = [quiet, ""]
    else:
        lines = [f"Curation review for snapshot `{snapshot}`.", ""]
    if settled:
        lines += [f"## Decisions available ({len(settled)})", ""]
        lines += [_fired_line(f) for f in settled] + [""]
    if new:
        lines += [f"## New this ingestion ({len(new)})", ""]
        lines += [_candidate_line(c) for c in new] + [""]
    if moved:
        lines += [f"## Evidence moved ({len(moved)})", ""]
        lines += [_fired_line(f) for f in moved] + [""]
    if unchanged:
        lines += [f"## Unchanged holds ({len(unchanged)})", ""]
        lines += [_held_line(ids, hold, named.get(ids)) for ids, hold in unchanged.items()]
        lines += [""]
        if hubs := _hubs(unchanged):
            lines += ["### Hubs", "",
                      "One decision closes several rows:", ""]
            lines += [f"- `{pid}`: held against {', '.join(f'`{p}`' for p in partners)}"
                      for pid, partners in hubs] + [""]
    # Unexamined is the figure that means "open this many questions" (issue
    # #228), and after the thorough passes it is what this ingestion introduced
    # and nothing else. The rest are what keep it that small.
    lines += ["## Counts", "",
              f"- unexamined: {len(new)}",
              f"- decisions available: {len(settled)}",
              f"- evidence moved: {len(moved)}",
              f"- unchanged holds: {len(unchanged)}",
              f"- curated: {recon['curated']}", ""]
    return "\n".join(lines)


def _candidate_line(candidate: dict) -> str:
    """One unexamined row, whichever queue it came from.

    A pair asks whether two ids are one person and a single id asks whether one
    id is two people (issue #232), so each prints the ids the question is about
    and the names that raised it.
    """
    return f"- {_ids(candidate.get('pilots') or [candidate['pilot']])}: {_describe(candidate)}"


def _held_line(ids: frozenset[str], hold: Hold, row: dict | None) -> str:
    """One held row: the ids, what the queue calls them, and its condition.

    ``row`` is the queue's own line for the pair, and is absent where the hold
    has dropped off the review list (a display name is a majority vote a new
    deck can flip, ADR 0007). The ids are what identifies a hold either way, so
    an unnamed one is listed rather than dropped.
    """
    fields = ([_describe(row)] if row else []) + [
        f"settles on `{hold.settles_on}`", f"as of `{hold.as_of}`"]
    return f"- {_ids(sorted(ids))}: " + ", ".join(fields)


def _describe(row: dict) -> str:
    """What the queue calls a row: the names that put it on the list.

    A pair carries how its two names relate, which is how much the shape can be
    trusted; one id carries the names its own decks recovered, the minorities
    being the whole question (ADR 0007).
    """
    if "relation" in row:
        return f"{row['display_name']} ({row['relation']})"
    others = [n for n in row["names"] if n != row["display_name"]]
    return f"{row['display_name']}, also {', '.join(others)}"


def _hubs(holds: Holds) -> list[tuple[str, list[str]]]:
    """Ids held against more than one partner, each with its partners.

    Where the same id recurs, one decision about that id retires every row it
    appears in, so the tail is worth reading top-down rather than row by row.
    """
    partners: dict[str, set[str]] = {}
    for ids in holds:
        for pid in ids:
            partners.setdefault(pid, set()).update(ids - {pid})
    return [(pid, sorted(theirs)) for pid, theirs in sorted(partners.items())
            if len(theirs) > 1]


def _ids(pilots: list[str]) -> str:
    return " + ".join(f"`{pid}`" for pid in pilots)


def _fired_line(fired: dict) -> str:
    return f"- {_ids(fired['pilots'])} ({fired['trigger']}): {fired['detail']}"
