"""Re-measure the figures ADR 0028 records, from the snapshots on disk (issue #273).

The rules that decide the clear identity cases at build (``graph7ph.identity_rules``)
set a deck bar per name shape and judge it against a matched stranger pool. The bars
are a choice under asymmetric loss and the ADR records what they do on the record:
per shape, the dictionary's merged and rejected pairs and how many of each the
composed rule reproduces or would wrongly merge; the cost of each guard; and the
stranger base rate at each bar, the out-of-sample instrument. Every one of those is
a property of the corpus, so this prints them again from the snapshots rather than
leaving the ADR's page as the only copy.

Two resolutions are read. With an empty dictionary the rules rule on every pair the
heuristics raise, which is the replay the suite also runs (``tests/test_identity_rules.py``)
and what the labelled figures come from. With the dictionary on file the output is
what the next build does: the pairs merged and separated by rule, and any rule the
dictionary overrode.

Usage (from the repo root, with the snapshots on disk)::

    uv run python scripts/identity_figures.py
    uv run python scripts/identity_figures.py --snapshots snapshots --to 20261104T091012Z
"""

import argparse
import collections
import itertools
from pathlib import Path

from graph7ph import identity_rules as rules
from graph7ph.build import reconcile
from graph7ph.curation import Curation, load_curation
from graph7ph.ingest import _snapshot_dirs, gate_sequence, load_checked
from graph7ph.repairs import apply_repairs, load_repairs

SHAPES = ("typo", "handle", "nickname", "first-name", "look-alike")
BARS = (0.4, 0.5, 0.8, 0.9, 1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--snapshots", type=Path, default=Path("snapshots"))
    parser.add_argument("--to", default=None, help="read the sequence up to this snapshot")
    args = parser.parse_args()

    dirs = [d for d in _snapshot_dirs(args.snapshots) if args.to is None or d.name <= args.to]
    repairs = load_repairs()
    snapshot = gate_sequence([apply_repairs(load_checked(d), repairs)[0] for d in dirs]).snapshot
    print(f"sequence to {dirs[-1].name}: {len(snapshot.decks)} decks")

    on_file = load_curation()
    groups: dict[str, set[str]] = {}
    for member, canonical in on_file.merges.items():
        groups.setdefault(canonical, {canonical}).add(member)
    merged = {frozenset(p) for ids in groups.values() for p in itertools.combinations(sorted(ids), 2)}
    apart = on_file.rejected | on_file.splits

    _, replay = reconcile(snapshot, Curation.empty(), None)
    ruled = {frozenset(p.pilots): p for p in replay.report.ruled_pairs}
    label = {k: "merge" if k in merged else "reject" if k in apart else "open" for k in ruled}
    print(f"pairs the heuristics raise with an empty dictionary: {len(ruled)}, "
          f"{collections.Counter(label.values())}")

    print("\nper shape: merged on file (reproduced by rule), rejected on file "
          "(bar alone would merge, composed rule merges)")
    for shape in SHAPES:
        m = [p for k, p in ruled.items() if label[k] == "merge" and p.shape == shape]
        r = [p for k, p in ruled.items() if label[k] == "reject" and p.shape == shape]
        print(f"  {shape}: {len(m)} ({sum(p.state == 'merged' for p in m)}), "
              f"{len(r)} ({sum(_bar_alone(p) for p in r)}, {sum(p.state == 'merged' for p in r)})")
    labelled_merges = [p for k, p in ruled.items() if label[k] == "merge"]
    labelled_rejects = [p for k, p in ruled.items() if label[k] == "reject"]
    print("states on the merged pairs:", dict(collections.Counter(p.state for p in labelled_merges)))
    print("states on the rejected pairs:", dict(collections.Counter(p.state for p in labelled_rejects)))
    print("rejected pairs without a shared event:",
          sum(1 for p in labelled_rejects if not p.shared_events), "of", len(labelled_rejects))
    print("merges the shared-event veto costs:",
          [(p.names, p.overlap, p.shared_events) for p in labelled_merges if p.shared_events and _bar_alone(p)])
    print("merges the bare-name clause costs:",
          [(p.names, p.overlap) for p in labelled_merges
           if p.state == "watching" and not p.shared_events and _bar_alone(p)])

    _, live = reconcile(snapshot, on_file, None)
    print("\nwith the dictionary on file:", dict(collections.Counter(p.state for p in live.report.ruled_pairs)))
    for p in live.report.ruled_pairs:
        if p.state != "watching":
            print(f"  {p.state}: {p.names[0]} / {p.names[1]} ({p.shape}) {p.overlap} on {p.archetype}"
                  + (f", {p.at_or_above} of {p.pool} strangers as high" if p.pool else "")
                  + (f", both at {', '.join(p.shared_events)}" if p.shared_events else ""))
    for p in live.report.rule_overrides:
        print(f"  overridden: {p.names[0]} / {p.names[1]} rule {p.state}, dictionary {p.overridden_by}")

    # The stranger base rate the build writes into the report (ADR 0028).
    print("\nstranger base rate (all gaps), as the build reports it:")
    for bar, share in live.report.stranger_base_rate.items():
        print(f"  at or above {bar}: {share:.4f}")


def _bar_alone(pair: rules.RuledPair) -> bool:
    return rules._clears(pair.shape, pair.overlap, pair.pool, pair.at_or_above)


if __name__ == "__main__":
    main()
