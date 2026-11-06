"""The post-ingestion curation brief: what this ingestion changed.

A review round used to start from `reconciliation.json`, which re-derives the
same flat queue every build and says nothing about what moved (issue #227). This
renders the same facts as a brief ranked on what changed, so a round starts from
a written page: holds the data has settled first, then every pair whose derived
state changed this round (merged by rule, separated, watching; issue #273), the
rules the dictionary overrode and the entries the build refused, then the
multi-name queue split into new and carried, and the unchanged holds as one line
each.

"Changed" is against the previous round, which the brief reads off the snapshot
sequence (the reconciliation every older snapshot yields) and never off the issue
that round filed (issue #271). Every figure here is derived from the report the
build just wrote, from that sequence and from the dictionary, never transcribed,
so nothing in the brief can go stale the way a checked-in count would (#227).
"""

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from graph7ph.curation import Hold
from graph7ph.pilots import SETTLING_TRIGGERS
from graph7ph.provenance import built_at, staleness

Holds = dict[frozenset[str], Hold]

# How `fetch` names a snapshot directory.
STAMP = "%Y%m%dT%H%M%SZ"

# The three states a candidate pair can be in, in the order the brief lists them
# (issue #273), each with its heading.
STATES = (("merged", "Merged by rule"), ("separated", "Separated"), ("watching", "Watching"))


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


def curation_report(
    recon: dict, holds: Holds, snapshot: str, previous: dict | None = None
) -> str:
    """The review brief for the bundle ``recon`` came from, as markdown.

    ``previous`` is the reconciliation the snapshot sequence before ``snapshot``
    yields (:func:`ingest.previous_reconciliation`), which is what the brief
    diffs against so it says what changed this round rather than what is open
    (issue #271); ``None`` where no snapshot precedes this one, so everything
    is listed as changed.
    """
    previous = previous or {}
    settled = [f for f in recon.get("fired_holds", []) if f["trigger"] in SETTLING_TRIGGERS]

    # A pair is listed under its state only where that state is not what the
    # sequence before this snapshot derived for it (issue #273): a pair the rules
    # merged last round and this round is a settled fact, not news. A pair the
    # previous sequence did not raise at all is news under whichever state it
    # took. Keyed on ids, since the names that raised a pair are a majority vote
    # a deck can flip (ADR 0007) while the pair stays.
    pairs = recon.get("ruled_pairs", [])
    before = {frozenset(p["pilots"]): p["state"] for p in previous.get("ruled_pairs", [])}
    changed = [p for p in pairs if before.get(frozenset(p["pilots"])) != p["state"]]
    blocks = [(title, [p for p in changed if p["state"] == state]) for state, title in STATES]
    # Listed every build, derived and never diffed: a dictionary edit is
    # invisible to a diff between two snapshot sequences read under the same
    # dictionary, so an override or a refusal would otherwise be news exactly
    # once and then silent.
    overrides = recon.get("rule_overrides", [])
    refused = recon.get("refused_entries", [])

    # The multi-name queue carries only what no pass has read (issue #232), which
    # is not the same as what this ingestion introduced: an id nobody read last
    # round either is the same question again, listed on that round's issue, so
    # it is carried and only the rest is new (issue #271).
    queue = recon.get("multi_name_ids", [])
    before_ids = {c["pilot"] for c in previous.get("multi_name_ids", [])}
    new = [c for c in queue if c["pilot"] not in before_ids]
    carried = [c for c in queue if c["pilot"] in before_ids]
    named = {h["pilot"]: h for h in recon.get("held_names", [])}
    listed = {frozenset(f["pilots"]) for f in settled}
    unchanged = {ids: hold for ids, hold in sorted(holds.items(), key=lambda kv: sorted(kv[0]))
                 if ids not in listed}

    # A brief filed after every ingestion is read only if its arrival means
    # something, so a round with no work says that in a sentence rather than in
    # a page of empty headings. The held tail is not work and does not make a
    # round loud, but it is still listed: a hold nobody lists is one nobody
    # retires. With no tail either there is nothing to print but the sentence.
    if not (settled or changed or overrides or refused or new or carried):
        quiet = (f"Nothing to review for snapshot `{snapshot}`: no pair changed state, "
                 "no rule was overridden, no entry was refused, no hold settled, and "
                 "no id is new or carried.")
        if not unchanged:
            return quiet + "\n"
        lines = [quiet, ""]
    else:
        lines = [f"Curation review for snapshot `{snapshot}`.", ""]
    if settled:
        lines += [f"## Decisions available ({len(settled)})", ""]
        lines += [_fired_line(f) for f in settled] + [""]
    for title, listed_pairs in blocks:
        if listed_pairs:
            lines += [f"## {title} ({len(listed_pairs)})", ""]
            lines += _pair_lines(listed_pairs) + [""]
    if overrides:
        lines += [f"## Rule overridden by the dictionary ({len(overrides)})", ""]
        lines += [_override_line(p) for p in overrides] + [""]
    if refused:
        lines += [f"## Entries refused ({len(refused)})", ""]
        lines += [f"- {r['kind']} `{r['key']}`: written against {r['written']!r}, "
                  f"now recovers {r['recovered']!r}" for r in refused] + [""]
    if new:
        lines += [f"## New this ingestion ({len(new)})", ""]
        lines += [_candidate_line(c) for c in new] + [""]
    if carried:
        lines += [f"## Carried over ({len(carried)})", ""]
        lines += [_candidate_line(c) for c in carried] + [""]
    if unchanged:
        lines += [f"## Unchanged holds ({len(unchanged)})", ""]
        lines += [_held_line(ids, hold, named.get(next(iter(ids))))
                  for ids, hold in unchanged.items()]
        lines += [""]
    # The three states in full beside what changed, so the figure that means
    # "this many pairs moved" sits next to the one that says where the whole
    # population stands; the rest are what keep the round small.
    states = Counter(p["state"] for p in pairs)
    moved = Counter(p["state"] for p in changed)
    lines += ["## Counts", ""]
    lines += [f"- {title.lower()}: {states[state]} (changed: {moved[state]})"
              for state, title in STATES]
    lines += [f"- overridden: {len(overrides)}",
              f"- refused: {len(refused)}",
              f"- decisions available: {len(settled)}",
              f"- new: {len(new)}",
              f"- carried: {len(carried)}",
              f"- unchanged holds: {len(unchanged)}",
              f"- curated: {recon['curated']}"]
    # The out-of-sample instrument, re-measured by this build (ADR 0028): what
    # strangers score at the bars the rules merge on, so the round can read a
    # merge against the population it sits in.
    if rate := recon.get("stranger_base_rate"):
        lines += ["- stranger base rate: " + ", ".join(
            f"{share:.1%} at {bar}" for bar, share in rate.items())]
    lines += [""]
    return "\n".join(lines)


def _pair_lines(pairs: list[dict]) -> list[str]:
    """One block's rows, an id recurring across pairs folded into one row.

    A bare first name against several surnamed partners raised one pair per
    partner (seven rows for one Ryan on #262), though whose deck it is is one
    question, so the recurring id is the row and its partners the detail, each
    with its best overlap (issues #271, #273). A pair is filed under whichever
    of its ids recurs more, the row sits where the hub's first pair did, and a
    pair with no recurring id is its own row.
    """
    degree = Counter(pid for p in pairs for pid in p["pilots"])
    hub_of = [max((pid for pid in p["pilots"] if degree[pid] > 1),
                  key=degree.get, default=None) for p in pairs]
    pairs_of: dict[str, list[dict]] = {}
    for hub, p in zip(hub_of, pairs):
        if hub is not None:
            pairs_of.setdefault(hub, []).append(p)
    lines: list[str] = []
    written: set[str] = set()
    for hub, p in zip(hub_of, pairs):
        if hub is None:
            lines.append(f"- {_ids(p['pilots'])}: {_describe(p)}")
        elif hub not in written:
            written.add(hub)
            name = p["names"][p["pilots"].index(hub)]
            lines.append(f"- `{hub}` ({name}) against {len(pairs_of[hub])}: " + "; ".join(
                _partner(pair, hub) for pair in pairs_of[hub]))
    return lines


def _partner(pair: dict, hub: str) -> str:
    """One partner of a hub: its id, its name and the evidence against the hub."""
    other = 1 - pair["pilots"].index(hub)
    return f"`{pair['pilots'][other]}` {pair['names'][other]} {_evidence(pair)}"


def _describe(pair: dict) -> str:
    """What the rules read on a pair: the names, the shape, and the evidence."""
    a, b = pair["names"]
    joiner = " = " if pair["state"] == "merged" else " / "
    parts = [f"{a}{joiner}{b} ({pair['shape']}), {_evidence(pair)}"]
    if pair["shared_events"]:
        parts.append("both at " + ", ".join(pair["shared_events"]))
    parts += [f"{pair['names'][pair['pilots'].index(pid)]} is a {why}"
              for pid, why in pair["hubs"].items()]
    if pair["canonical"]:
        parts.append(f"node `{pair['canonical']}`")
    return ", ".join(parts)


def _evidence(pair: dict) -> str:
    """The deck evidence in one phrase: the overlap, its archetype and its pool."""
    if pair["archetype"] is None:
        return "no deck on a shared archetype at different events"
    text = f"{pair['overlap']:.2f} on {pair['archetype']}"
    if pair["pool"] is not None:
        text += f" ({pair['at_or_above']} of {pair['pool']} strangers as high)"
    return text


def _override_line(pair: dict) -> str:
    """One overridden pair: what the rule said and the dictionary kind that beat it.

    A merge held back through a chain (issue #288) names the pair the ruling is
    on, which is not this one.
    """
    if pair.get("kept_apart"):
        verb = f"on {_ids(pair['kept_apart'])} keeps the chain apart"
    else:
        verb = "keeps them apart" if pair["overridden_by"] in ("reject", "split") else "folds them"
    return (f"- {_ids(pair['pilots'])}: {_describe(pair)}; the rule says "
            f"{pair['state']}, a [[{pair['overridden_by']}]] {verb}")


def _candidate_line(candidate: dict) -> str:
    """One unexamined multi-name id: the id, the name that won it, and the rest.

    One id asks whether its decks are one person (issue #232), so it prints the
    id and every name its decks recovered, the minorities being the whole
    question (ADR 0007).
    """
    return f"- `{candidate['pilot']}`: {_also(candidate)}"


def _held_line(ids: frozenset[str], hold: Hold, row: dict | None) -> str:
    """One held id: what the queue calls it, and its condition.

    ``row`` is the queue's own line for the id, and is absent where the hold
    has dropped off the review list (a display name is a majority vote a new
    deck can flip, ADR 0007). The id is what identifies a hold either way, so
    an unnamed one is listed rather than dropped.
    """
    fields = ([_also(row)] if row else []) + [
        f"settles on `{hold.settles_on}`", f"as of `{hold.as_of}`"]
    return f"- {_ids(sorted(ids))}: " + ", ".join(fields)


def _also(row: dict) -> str:
    """The name that won an id and every other name its decks recovered."""
    others = [n for n in row["names"] if n != row["display_name"]]
    return f"{row['display_name']}, also {', '.join(others)}"


def _ids(pilots: list[str]) -> str:
    return " + ".join(f"`{pid}`" for pid in pilots)


def _fired_line(fired: dict) -> str:
    return f"- {_ids(fired['pilots'])} ({fired['trigger']}): {fired['detail']}"
