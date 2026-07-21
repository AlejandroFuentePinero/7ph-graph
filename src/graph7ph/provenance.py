"""Build provenance: what the artifact on disk was built from.

The baseline gate re-runs today's queries against whatever bundle is sitting in
``data/graph``. Query-layer changes are therefore graded honestly even against a
stale artifact, but everything living *inside* the artifact (ingest, build,
schema, curation) is not: change one, skip the rebuild, and the gate reports a
green "no regression" about code it never ran (issue #55).

A hash of the database cannot close that gap: the file is not byte-reproducible,
so two builds of identical data differ (measured during #50). What is stable is
the *input* side, so the bundle carries a digest of the sources it was built
from, and the gate refuses to grade a bundle whose digest is not the one the
working tree would produce today.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from graph7ph.curation import CURATION_PATH

PACKAGE_DIR = Path(__file__).parent

# The modules whose content ends up baked into the artifact. Deliberately not the
# whole package: query, render, explore, app, baseline and fetch are read live at
# gate time or are not part of a build at all, so digesting them would demand a
# rebuild for changes the gate already grades correctly.
#
# Granularity is the whole file, which errs towards refusing: `db.py` holds the
# reader the app uses as well as the writer the build uses, so editing the reader
# demands a rebuild it cannot actually affect. Accepted, because the failure it
# trades against is a bundle wrongly graded green, and a spurious rebuild costs
# a minute.
BUILD_INPUTS = (
    "build.py",
    "curation.py",
    "db.py",
    "ingest.py",
    "models.py",
    "pilots.py",
)


def source_digest(
    package: Path = PACKAGE_DIR, curation: Path = CURATION_PATH
) -> str:
    """A digest over the sources a build turns into an artifact.

    Content, not modification time: ``git checkout`` and ``git stash`` rewrite
    mtimes wholesale, so an mtime comparison would refuse to grade artifacts that
    are perfectly current, and a gate that cries wolf gets switched off.

    The curated dictionary is a build input as much as the code is (ADR 0005), and
    is folded in by content too. An absent one digests as empty rather than
    raising, because that is exactly what a build does with it: `load_curation`
    treats a missing dictionary as an empty one and builds an uncurated graph. So
    the two cases are two different artifacts, and folding the absence in is what
    lets the digest tell them apart. It follows that the digest inherits
    `CURATION_PATH`'s relative resolution, as every other default path in the CLI
    does.
    """
    digest = hashlib.sha256()
    for name, path in [(n, Path(package) / n) for n in BUILD_INPUTS] + [
        ("curation", Path(curation))
    ]:
        blob = path.read_bytes() if path.exists() else b""
        # Length-prefixed, so the boundary between one file and the next name is
        # unambiguous. Concatenating them plainly lets content absorb the marker
        # that follows it, and two different trees hash the same (measured: a
        # `build.py` of `x` beside a `curation.py` of `ycuration.pyz` collides
        # with `xcuration.pyy` beside `z`).
        digest.update(f"{name}:{len(blob)}:".encode())
        digest.update(blob)
    return digest.hexdigest()


def provenance_path(artifact: Path) -> Path:
    """Where the build stamp is written for the bundle at ``artifact``.

    Inside the artifact directory beside the two reports, so it promotes and rolls
    back with the graph as one bundle (ADR 0008): a restored backup carries the
    provenance of the graph it restores, not of the build that replaced it.
    """
    return Path(artifact) / "provenance.json"


def staleness(artifact: Path) -> str | None:
    """Why the bundle at ``artifact`` cannot be relied on, or ``None`` if it can.

    Returns prose rather than a bool because the answer is only useful if the
    caller can say what is wrong: an artifact built from other sources and an
    artifact carrying no provenance at all are both unusable, for different
    reasons and with the same remedy.

    The prose is a fragment, and says nothing about what the caller wanted the
    bundle for: each supplies its own framing, because the two that ask are
    asking about the same defect for different ends (the baseline gate refuses
    to grade it, the deploy script refuses to ship it, issue #63).
    """
    path = provenance_path(artifact)
    if not path.exists():
        return (
            f"the bundle at {artifact} carries no build provenance "
            "(it predates issue #55): run `uv run graph7ph build` to stamp one"
        )
    # An unreadable stamp is exactly as unusable as a wrong one, and both callers
    # are pass/fail steps where a crash and a refusal must not look alike, so a
    # half-written stamp (`write_text` is not atomic) reads as stale rather than
    # spilling a traceback. The deploy script is the strict one: it runs this
    # under `set -e`, where a traceback aborts with no message of its own.
    # Parsing is not the same question as being a stamp, so both are asked: a file
    # can be good JSON and still be `null` or a list, with no fields to read off.
    # Same reason `get` is used for the fields below.
    try:
        recorded = json.loads(path.read_text())
    except json.JSONDecodeError:
        recorded = None
    if not isinstance(recorded, dict):
        return (
            f"the bundle at {artifact} carries a build stamp that cannot be read: "
            "run `uv run graph7ph build` to write a fresh one"
        )
    if recorded.get("source_digest") != source_digest():
        return (
            f"the bundle at {artifact} was built at "
            f"{recorded.get('built_at', 'an unrecorded time')} from "
            "sources that are not the ones in the working tree: "
            "run `uv run graph7ph build` before using it"
        )
    return None


def stamp(artifact: Path, *, reproducible: bool = True) -> None:
    """Record what the bundle at ``artifact`` was just built from.

    ``reproducible=False`` records no digest, for a build handed its curation in
    code rather than reading the checked-in dictionary: no state of the working
    tree reproduces that bundle, so there is nothing honest to record and it must
    never read as gradeable. A null digest matches no tree, so the gate refuses it
    without needing a case of its own.
    """
    provenance_path(artifact).write_text(
        json.dumps(
            {
                "source_digest": source_digest() if reproducible else None,
                "built_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )
