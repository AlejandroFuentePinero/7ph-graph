"""Hugging Face Space / Colab entrypoint: serve the explorer over a prebuilt graph.

The app only ever reads an already-promoted artifact (ADR 0003); it never fetches
or builds, so the deployed Space carries no upstream credential and no ingestion
code path. ``GRAPH7PH_DB`` points at the artifact, which the deploy step uploads
alongside this file.
"""

from graph7ph.app import build_app
from graph7ph.db import artifact_path, database_path
from graph7ph.serve import APP_KWARGS

DB_PATH = artifact_path()

# The artifact is a directory holding the database beside its reports, so an
# existing directory is not proof of a graph: the database inside it is.
if not database_path(DB_PATH).exists():
    raise SystemExit(
        f"No graph artifact at {DB_PATH}. Build one with 'uv run graph7ph build', "
        "or point GRAPH7PH_DB at a promoted artifact."
    )

demo = build_app(DB_PATH)

if __name__ == "__main__":
    demo.launch(app_kwargs=APP_KWARGS)
