"""The app's own record of its use (issue #356).

A listener in front of the app notes each page served and each view drawn, a
writer saves the notes as small files in a folder that outlives the Space, and a
report reads a copy of that folder at home. What is held here is what the record
may never do: change what a visitor is sent, fail a request, or keep what a
visitor looked up.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

import gradio as gr
import pytest
from starlette.middleware import Middleware
from starlette.responses import Response
from starlette.testclient import TestClient

from graph7ph import usage
from graph7ph.app import build_app, launch_kwargs
from graph7ph.build import build_graph
from graph7ph.models import load_snapshot
from graph7ph.serve import APP_KWARGS

_VIEWS = frozenset({"card_overview"})


def _lines(folder: Path) -> list[dict]:
    """Every recorded line under ``folder``, in the order the files were written."""
    return [
        json.loads(line)
        for path in sorted(folder.rglob("*.jsonl"))
        for line in path.read_text().splitlines()
    ]


def _fn(demo: gr.Blocks, name: str) -> int:
    """The number the page sends for the function named ``name``."""
    return next(d["id"] for d in demo.config["dependencies"] if d["api_name"] == name)


@pytest.fixture
def recorded(tmp_path):
    """A stand-in page with one drawing function, recorded into a folder."""
    folder = tmp_path / "usage"
    folder.mkdir()
    recorder = usage.Recorder(folder, views=_VIEWS)

    def draw_cards(view, card):
        return card

    with gr.Blocks() as demo:
        view = gr.Textbox()
        card = gr.Textbox()
        gr.Button().click(draw_cards, [view, card], gr.Textbox())
    demo.launch(
        prevent_thread_lock=True,
        app_kwargs={
            "middleware": [Middleware(usage.RecordVisits, recorder=recorder)]
            + APP_KWARGS["middleware"]
        },
    )
    yield TestClient(demo.app), recorder, folder, demo
    recorder.close()
    demo.close()


def test_a_page_served_is_one_line(recorded):
    client, recorder, folder, _ = recorded

    client.get("/", headers={
        "host": "www.7phgraph.com",
        "referer": "https://discord.com/channels/1/2?from=someone",
        "sec-fetch-dest": "document",
    })
    recorder.flush()

    (line,) = _lines(folder)
    assert line["kind"] == "page"
    assert line["host"] == "www.7phgraph.com"
    # The site a visitor came from, never the page on it or anything after a `?`.
    assert line["ref"] == "discord.com"
    assert line["dest"] == "document"
    assert line["v"] == 1
    assert datetime.fromisoformat(line["t"]).tzinfo is not None


def test_the_first_page_of_a_run_names_the_headers_that_reached_the_app(recorded):
    """Which headers the Hub's proxy passes on cannot be seen from outside (#356,
    measurement 3). Their names, once per run, never their values."""
    client, recorder, folder, _ = recorded

    client.get("/", headers={"x-secret-thing": "do-not-keep"})
    client.get("/")
    recorder.flush()

    first, second = _lines(folder)
    assert "x-secret-thing" in first["names"]
    assert "names" not in second
    assert "do-not-keep" not in "".join(p.read_text() for p in folder.rglob("*.jsonl"))


def test_a_draw_is_one_line_and_keeps_nothing_the_visitor_asked_for(recorded):
    client, recorder, folder, demo = recorded

    client.post("/gradio_api/queue/join", headers={"host": "www.7phgraph.com"}, json={
        "data": ["card_overview", "Brainstorm of the Ancients"],
        "fn_index": _fn(demo, "draw_cards"),
        "session_hash": "visit-one",
        "trigger_id": 1,
    })
    recorder.flush()

    (line,) = _lines(folder)
    assert (line["kind"], line["name"], line["view"]) == ("draw", "draw_cards", "card_overview")
    assert line["session"] == "visit-one"
    assert line["host"] == "www.7phgraph.com"
    written = "".join(p.read_text() for p in folder.rglob("*.jsonl"))
    assert "Brainstorm" not in written


def test_a_view_is_recorded_only_when_it_is_one_of_the_apps_own(recorded):
    """The view is read out of the inputs, which also hold names. So an input is
    kept only when it is exactly one of the app's view keys."""
    client, recorder, folder, demo = recorded

    client.post("/gradio_api/queue/join", json={
        "data": ["Some Pilot", "card_overview_but_not_quite"],
        "fn_index": _fn(demo, "draw_cards"),
        "session_hash": "visit-two",
    })
    recorder.flush()

    (line,) = _lines(folder)
    assert line["view"] is None
    assert line["name"] == "draw_cards"


def test_the_apps_own_launch_requests_are_not_visits(recorded):
    """Launching makes two requests of its own before any visitor arrives."""
    client, recorder, folder, _ = recorded

    client.head("/")
    client.get("/gradio_api/startup-events")
    recorder.flush()

    assert _lines(folder) == []


def test_a_script_running_browser_leaves_a_second_mark(recorded):
    """A fetcher that runs no script asks for the page and nothing else. The
    stylesheet is the first thing the page's script asks for."""
    client, recorder, folder, _ = recorded

    client.get("/theme.css", headers={"host": "www.7phgraph.com"})
    recorder.flush()

    (line,) = _lines(folder)
    assert (line["kind"], line["host"]) == ("browser", "www.7phgraph.com")


def test_a_folder_that_cannot_be_written_never_reaches_a_request(recorded, capsys):
    client, recorder, folder, _ = recorded
    healthy = client.get("/")
    recorder.flush()
    shutil.rmtree(folder)
    folder.write_text("no longer a folder")

    broken = client.get("/")
    recorder.flush()

    assert broken.status_code == 200
    assert len(broken.content) == len(healthy.content)
    assert "USAGE RECORD NOT WRITTEN" in capsys.readouterr().out

    # Nothing noted while it was down is lost once it is back.
    folder.unlink()
    folder.mkdir()
    recorder.flush()
    assert [line["kind"] for line in _lines(folder)] == ["page"]


def test_a_note_the_writer_cannot_handle_costs_that_note_and_says_so(tmp_path, capsys):
    """The writer runs on its own thread, where an error would end the record
    with nothing said. Found the hard way: the first draft died on its first draw."""
    recorder = usage.Recorder(tmp_path, views=_VIEWS)
    try:
        recorder.note({"kind": "page", "host": object()})
        recorder.flush()
        assert "USAGE RECORD NOT WRITTEN" in capsys.readouterr().out

        recorder.note({"kind": "page", "host": "h"})
        recorder.flush()
    finally:
        recorder.close()

    assert [line["host"] for line in _lines(tmp_path)] == ["h"]


def test_a_full_queue_drops_notes_and_says_how_many(tmp_path):
    recorder = usage.Recorder(tmp_path, views=_VIEWS)
    try:
        for _ in range(usage.QUEUE_SIZE + 7):
            recorder.note({"kind": "page", "host": "h"})
        recorder.flush()
    finally:
        recorder.close()

    lines = _lines(tmp_path)
    assert sum(line["kind"] == "page" for line in lines) == usage.QUEUE_SIZE
    assert [line["n"] for line in lines if line["kind"] == "dropped"] == [7]


def test_nothing_is_written_when_nothing_happened(tmp_path):
    recorder = usage.Recorder(tmp_path, views=_VIEWS)
    recorder.flush()
    recorder.close()

    assert list(tmp_path.iterdir()) == []


def test_a_request_and_its_answer_pass_through_untouched(tmp_path):
    """The listener reads a copy. What the app receives and what it sends back are
    the same messages, in the same pieces."""
    received = []

    async def echo(scope, receive, send):
        while True:
            message = await receive()
            received.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        await Response(b"answer", headers={"x-kept": "yes"})(scope, receive, send)

    recorder = usage.Recorder(tmp_path, views=_VIEWS)
    try:
        client = TestClient(usage.RecordVisits(echo, recorder=recorder))

        def pieces():
            yield b'{"fn_index": 1, '
            yield b'"session_hash": "s", "data": []}'

        response = client.post("/gradio_api/queue/join", content=pieces())
    finally:
        recorder.close()

    assert b"".join(received) == b'{"fn_index": 1, "session_hash": "s", "data": []}'
    assert (response.content, response.headers["x-kept"]) == (b"answer", "yes")


def test_a_body_too_large_to_be_a_draw_reaches_the_app_whole_and_is_not_kept(tmp_path):
    """A waiting note holds a copy of its request, so the copy is capped: a flood
    of large posts must not be able to fill memory. The app still gets all of it."""
    received = []

    async def echo(scope, receive, send):
        while True:
            message = await receive()
            received.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        await Response(b"ok")(scope, receive, send)

    sent = json.dumps({"fn_index": 1, "session_hash": "s", "data": ["x" * 20_000]}).encode()
    recorder = usage.Recorder(tmp_path, views=_VIEWS)
    try:
        TestClient(usage.RecordVisits(echo, recorder=recorder)).post(
            "/gradio_api/queue/join", content=sent
        )
        recorder.flush()
    finally:
        recorder.close()

    assert b"".join(received) == sent
    (line,) = _lines(tmp_path)
    assert (line["kind"], line["fn"], line["session"]) == ("draw", None, None)


def test_recording_is_off_unless_the_folder_is_named(monkeypatch):
    monkeypatch.delenv(usage.FOLDER_VARIABLE, raising=False)

    assert usage.recording(APP_KWARGS, views=_VIEWS) is APP_KWARGS


def test_a_named_folder_that_is_not_there_leaves_the_app_as_it_was(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(usage.FOLDER_VARIABLE, str(tmp_path / "never-mounted"))

    assert usage.recording(APP_KWARGS, views=_VIEWS) is APP_KWARGS
    assert "USAGE RECORD OFF" in capsys.readouterr().out


def test_a_named_folder_puts_the_listener_in_front_of_the_apps_own(monkeypatch, tmp_path):
    monkeypatch.setenv(usage.FOLDER_VARIABLE, str(tmp_path))

    stack = usage.recording(APP_KWARGS, views=_VIEWS)["middleware"]
    try:
        assert stack[0].cls is usage.RecordVisits
        assert stack[1:] == APP_KWARGS["middleware"]
    finally:
        stack[0].kwargs["recorder"].close()


@pytest.fixture
def real_app(tmp_path, snapshot_dir):
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    return build_app(artifact)


def _served(demo: gr.Blocks) -> tuple[bytes, bytes]:
    """The page and its config as one launch of ``demo`` serves them."""
    kwargs = launch_kwargs()
    demo.launch(prevent_thread_lock=True, **kwargs)
    try:
        client = TestClient(demo.app)
        return client.get("/").content, client.get("/config").content
    finally:
        demo.close()
        listener = kwargs["app_kwargs"]["middleware"][0]
        if listener.cls is usage.RecordVisits:
            listener.kwargs["recorder"].close()


def test_the_real_page_and_config_are_the_same_bytes_recorded_or_not(
    real_app, monkeypatch, tmp_path
):
    folder = tmp_path / "usage"
    folder.mkdir()
    monkeypatch.delenv(usage.FOLDER_VARIABLE, raising=False)
    plain = _served(real_app)

    monkeypatch.setenv(usage.FOLDER_VARIABLE, str(folder))
    recorded = _served(real_app)

    assert recorded == plain
    # And the second launch did record: the comparison is not of two plain apps.
    assert [line["kind"] for line in _lines(folder)] == ["start", "page"]


def test_a_pilot_looked_up_in_the_real_app_is_in_no_recorded_line(
    real_app, monkeypatch, tmp_path
):
    folder = tmp_path / "usage"
    folder.mkdir()
    monkeypatch.setenv(usage.FOLDER_VARIABLE, str(folder))
    kwargs = launch_kwargs()
    real_app.launch(prevent_thread_lock=True, **kwargs)
    recorder = kwargs["app_kwargs"]["middleware"][0].kwargs["recorder"]
    try:
        TestClient(real_app.app).post("/gradio_api/queue/join", json={
            "data": ["pilot_overview", "A Pilot Nobody Should Find"],
            "fn_index": _fn(real_app, "draw_pilots"),
            "session_hash": "visit-three",
        })
        recorder.flush()
    finally:
        real_app.close()
        recorder.close()

    start, line = _lines(folder)
    assert start["kind"] == "start"
    assert (line["name"], line["view"]) == ("draw_pilots", "pilot_overview")
    assert "Nobody" not in "".join(p.read_text() for p in folder.rglob("*.jsonl"))


def _write(folder: Path, name: str, lines: list[dict]) -> None:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"v": 1, "run": "r", **line}) + "\n" for line in lines))


def test_the_report_counts_a_copy_of_the_folder_by_week(tmp_path):
    """Expected figures counted by hand from the lines below.

    Week of Monday 2026-11-02: three pages (two navigations, one stylesheet), two
    draws by one visit. Week of 2026-11-09: one page, one draw by another visit.
    A function wired to several controls is counted under its one name, and the
    line each start of the app writes is not a visit.
    A listing runs from the most to the fewest, and equal counts in label order.
    One pick is several calls (seen in a browser: `cards_drawing`, `draw_cards`,
    then an unnamed tidy-up), and only the one that draws is a draw. The other
    two of visit s1 are listed apart and are not what its visit ended on.
    """
    www, own = "www.7phgraph.com", "alejandrofupi-7ph-graph.hf.space"
    _write(tmp_path, "2026/11/03/a.jsonl", [
        {"t": "2026-11-03T10:00:00+00:00", "kind": "page", "host": www, "ref": "discord.com", "dest": "document"},
        {"t": "2026-11-03T10:00:01+00:00", "kind": "browser", "host": www},
        {"t": "2026-11-03T10:00:59+00:00", "kind": "draw", "host": www, "session": "s1", "fn": 3, "name": "cards_drawing", "view": None},
        {"t": "2026-11-03T10:01:00+00:00", "kind": "draw", "host": www, "session": "s1", "fn": 4, "name": "draw_cards", "view": "card_overview"},
        {"t": "2026-11-03T10:02:00+00:00", "kind": "draw", "host": www, "session": "s1", "fn": 9, "name": "draw_landscape_1", "view": None},
        {"t": "2026-11-03T10:02:01+00:00", "kind": "draw", "host": www, "session": "s1", "fn": 10, "name": "lambda_10", "view": None},
        {"t": "2026-11-04T09:00:00+00:00", "kind": "page", "host": own, "ref": None, "dest": "document"},
        {"t": "2026-11-08T23:59:59+00:00", "kind": "page", "host": www, "ref": None, "dest": None},
    ])
    _write(tmp_path, "2026/11/09/b.jsonl", [
        {"t": "2026-11-08T23:00:00+00:00", "kind": "start"},
        {"t": "2026-11-09T00:00:00+00:00", "kind": "page", "host": www, "ref": "discord.com", "dest": "document"},
        {"t": "2026-11-09T00:01:00+00:00", "kind": "draw", "host": www, "session": "s2", "fn": 4, "name": "draw_cards_3", "view": "card_overview"},
        {"t": "2026-11-09T00:02:00+00:00", "kind": "dropped", "n": 3},
    ])

    assert usage.report(tmp_path, by="week") == """\
Usage by week (UTC), 2026-11-03 to 2026-11-09

Week of     Pages  Navigations  Stylesheets  Draws  Visits that drew
2026-11-02      3            2            1      2                 1
2026-11-09      1            1            0      1                 1

Pages: every time the page was served. Navigations: of those, the ones a browser
asked for as a page. Stylesheets: page loads whose script then ran. The last two
are two readings of "a real browser"; neither is settled as the measure yet.

Address
  www.7phgraph.com                  3
  alejandrofupi-7ph-graph.hf.space  1

Came from
  (not said)   2
  discord.com  2

Drawn
  card_overview   2
  draw_landscape  1

Last thing drawn in a visit
  card_overview   1
  draw_landscape  1

Other calls
  cards_drawing  1
  lambda         1

RECORDER DROPPED 3 NOTES: the queue was full, so these counts are short.
"""


def test_the_report_counts_by_day_when_asked(tmp_path):
    _write(tmp_path, "a.jsonl", [
        {"t": "2026-11-03T10:00:00+00:00", "kind": "page", "host": "h", "ref": None, "dest": None},
        {"t": "2026-11-04T10:00:00+00:00", "kind": "page", "host": "h", "ref": None, "dest": None},
    ])

    table = usage.report(tmp_path, by="day").split("\n\n")[1]

    assert table == """\
Day         Pages  Navigations  Stylesheets  Draws  Visits that drew
2026-11-03      1            0            0      0                 0
2026-11-04      1            0            0      0                 0"""


def test_the_report_says_so_when_the_folder_holds_nothing(tmp_path):
    assert usage.report(tmp_path, by="week") == f"No usage recorded under {tmp_path}.\n"
