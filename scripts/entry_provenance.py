"""Stamp every dictionary entry that applies to the graph with its provenance (issue #283).

Upstream reuses a pilot id for a different person, so each ``merge``, ``reject``,
``split``, ``name``, ``deck_pilot``, ``deck_archetype`` and ``deck_event`` entry in
``curation/pilots.toml`` carries the snapshot it was written on (``as_of``) and the
display name each of its ids recovered then (``names``), and the build refuses an
entry whose id no longer recovers its written name (ADR 0005). This fills the two
fields in for every entry still lacking them, from the earliest local snapshot
that holds every id the entry names: the sequence starts 2026-07-13, before both
upstream rewrites, so that is the name the entry was written against. An entry
already carrying the fields is left as it is. Comments and the rest of the file
are untouched; the result is re-parsed and checked against the original before
it is written.

Usage (from the repo root, with the snapshots on disk)::

    uv run python scripts/entry_provenance.py
    uv run python scripts/entry_provenance.py --dictionary curation/pilots.toml --snapshots snapshots
"""

import argparse
import re
import tomllib
from pathlib import Path

from graph7ph.curation import APPLIED_KINDS, CURATION_PATH, _entry_ids
from graph7ph.ingest import _snapshot_dirs, load_checked
from graph7ph.pilots import recovered_names
from graph7ph.repairs import apply_repairs, load_repairs

HEADER = re.compile(r"^\[\[(\w+)\]\]\s*$")
KEY = re.compile(r"^\w+\s*=")


def fill_provenance(text: str, snapshots: list[tuple[str, dict[str, str]]]) -> tuple[str, int]:
    """The file with every unstamped applying entry stamped, and how many were.

    ``snapshots`` is each snapshot's name with what every id recovered in it,
    oldest first. Each table of the TOML text is matched to its parsed entry by
    position, so the fields land inside the right table, after its last key.
    """
    raw = tomllib.loads(text)
    lines = text.splitlines()
    out: list[str] = []
    seen: dict[str, int] = {}
    kind, index, last_key = None, -1, -1  # last_key: index into `out`
    stamped = 0

    def close() -> None:
        nonlocal stamped
        if kind not in APPLIED_KINDS:
            return
        table = raw[kind][index]
        if "as_of" in table and "names" in table:
            return
        ids = sorted(_entry_ids(kind, table))
        held = next((s for s in snapshots if all(i in s[1] for i in ids)), None)
        if held is None:
            raise SystemExit(f"no snapshot holds every id of [[{kind}]] {ids}")
        as_of, recovered = held
        names = ", ".join(f'"{i}" = "{recovered[i]}"' for i in ids)
        out.insert(last_key + 1, f"names = {{ {names} }}")
        out.insert(last_key + 1, f'as_of = "{as_of}"')
        stamped += 1

    for line in lines:
        if header := HEADER.match(line):
            close()
            kind = header.group(1)
            index = seen[kind] = seen.get(kind, -1) + 1
        elif KEY.match(line):
            last_key = len(out)
        out.append(line)
    close()
    result = "\n".join(out) + "\n"
    # The fill must add the two fields and change nothing else.
    for k, tables in tomllib.loads(result).items():
        for before, after in zip(raw[k], tables, strict=True):
            added = set(after) - set(before)
            if added - {"as_of", "names"} or any(before[key] != after[key] for key in before):
                raise SystemExit(f"[[{k}]] changed beyond its provenance: {before} -> {after}")
    return result, stamped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dictionary", type=Path, default=CURATION_PATH)
    parser.add_argument("--snapshots", type=Path, default=Path("snapshots"))
    args = parser.parse_args()
    repairs = load_repairs()
    snapshots = [
        (d.name, recovered_names(apply_repairs(load_checked(d), repairs)[0].decks))
        for d in _snapshot_dirs(args.snapshots)
    ]
    text, stamped = fill_provenance(args.dictionary.read_text(), snapshots)
    args.dictionary.write_text(text)
    print(f"stamped {stamped} entr{'y' if stamped == 1 else 'ies'} in {args.dictionary}")


if __name__ == "__main__":
    main()
