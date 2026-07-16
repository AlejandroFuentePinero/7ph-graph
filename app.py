"""Hugging Face Space / Colab entrypoint: serve the explorer over a prebuilt graph.

The app only ever reads an already-promoted artifact (ADR 0003); it never fetches
or builds, so the deployed Space carries no upstream credential and no ingestion
code path. ``GRAPH7PH_DB`` points at the artifact, which the deploy step uploads
alongside this file.
"""

from graph7ph.app import build_app
from graph7ph.db import artifact_path

DB_PATH = artifact_path()

if not DB_PATH.is_dir():
    raise SystemExit(
        f"No graph artifact at {DB_PATH}. Build one with 'uv run graph7ph build', "
        "or point GRAPH7PH_DB at a promoted artifact."
    )

demo = build_app(DB_PATH)

if __name__ == "__main__":
    demo.launch()
