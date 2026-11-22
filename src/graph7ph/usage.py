"""The app's own record of its use (issue #356, findings in docs/research/usage-series.md).

Nothing else counts visits to this app. The public address is not proxied, so the
DNS provider sees no request; the Hub's visit count has no API and no dates; and
the Space's run log holds no requests. So the app notes its own: a page served,
and a view drawn. That is all it can see without script in the page, and the page
carries none.

Three rules decide the shape of everything here.

**A visitor is never affected.** The listener reads requests and touches no
response. A note is put on a queue and the request goes on; a full queue drops
the note. Writing happens on another thread, and a folder that cannot be written
costs notes, never a request.

**What a visitor looked up is never kept.** A draw's inputs hold the card or the
pilot asked for. They are read for one thing, the view key, and that is kept only
when it is exactly one of the app's own. No address is kept, so the record counts
visits and cannot count people.

**Each batch is a new file.** The folder is a Storage Bucket mounted into the
Space, which is object storage: a file written once and closed is what it is
built for, and appending to one is not something its documentation describes. A
batch not yet written when the Space stops is lost, at most ``FLUSH_SECONDS`` of
notes.

The record is off unless ``GRAPH7PH_USAGE_DIR`` names a folder that exists, which
is true on the Space and nowhere else.
"""

import atexit
import json
import os
import queue
import re
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from starlette.middleware import Middleware
from starlette.types import ASGIApp, Receive, Scope, Send

FOLDER_VARIABLE = "GRAPH7PH_USAGE_DIR"

# What a redeploy can cost: the notes of the last five minutes. A shorter wait
# buys little and writes more, smaller files.
FLUSH_SECONDS = 300

QUEUE_SIZE = 10_000

# A draw's body measured about 150 bytes. Past this it is not one, and is not
# read. The cap is also what bounds memory: a full queue of capped bodies is 80 MB.
_BODY_CAP = 8_192


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Recorder:
    """Takes notes from the listener and writes them out, a batch at a time."""

    def __init__(self, folder: Path, *, views: frozenset[str]) -> None:
        self.folder = Path(folder)
        self.views = views
        # Which start of the app wrote a line: the gaps between runs are restarts.
        self.run = uuid.uuid4().hex[:8]
        self._notes: queue.Queue[dict] = queue.Queue(maxsize=QUEUE_SIZE)
        self._dropped = 0
        self._counting = threading.Lock()
        self._unwritten: list[str] = []
        self._files = 0
        self._failing = False
        self._names: dict[int, str] | None = None
        self._flushing = threading.Lock()
        self._stop = threading.Event()
        self._writer = threading.Thread(target=self._write_on_time, daemon=True)
        self._writer.start()

    def note(self, event: dict) -> None:
        """On the request's own path, so it neither waits nor raises."""
        try:
            self._notes.put_nowait(event)
        except queue.Full:
            with self._counting:
                self._dropped += 1

    def flush(self) -> None:
        """Write what has been noted as one new file.

        Never raises. It runs on the writer's thread, where an error would end the
        record with nothing said, so a failure of any kind is said once, loudly,
        in the run log. Lines already made are kept for the next attempt.
        """
        with self._flushing:
            try:
                self._take_notes()
                if not self._unwritten:
                    return
                now = datetime.now(timezone.utc)
                path = (
                    self.folder / f"{now:%Y/%m/%d}"
                    / f"{now:%H%M%S}-{self.run}-{self._files:06d}.jsonl"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("".join(line + "\n" for line in self._unwritten))
            except Exception as error:  # noqa: BLE001
                if not self._failing:
                    print(f"\n*** USAGE RECORD NOT WRITTEN to {self.folder}: {error!r}. "
                          "The app is unaffected; visits are not being recorded. ***\n",
                          flush=True)
                self._failing = True
                # Held for the next attempt, up to twice what the queue holds.
                del self._unwritten[: -2 * QUEUE_SIZE]
                return
            self._failing = False
            self._files += 1
            self._unwritten = []

    def close(self) -> None:
        self._stop.set()
        self.flush()

    def _write_on_time(self) -> None:
        while not self._stop.wait(FLUSH_SECONDS):
            self.flush()

    def _take_notes(self) -> None:
        while True:
            try:
                note = self._notes.get_nowait()
            except queue.Empty:
                break
            if note["kind"] == "draw":
                drawn = self._what_was_drawn(note.pop("body"), note.pop("app"))
                note = {**note, **drawn}
            self._unwritten.append(json.dumps({"v": 1, "run": self.run, **note}))
        with self._counting:
            dropped, self._dropped = self._dropped, 0
        if dropped:
            self._unwritten.append(json.dumps(
                {"v": 1, "run": self.run, "t": _now(), "kind": "dropped", "n": dropped}
            ))

    def _what_was_drawn(self, body: bytes, app) -> dict:
        """The function, the visit and the view out of a draw's body. The body is
        whatever a client chose to send, so nothing in it is trusted to be there,
        and nothing else in it is kept."""
        try:
            sent = json.loads(body)
        except ValueError:
            sent = None
        if not isinstance(sent, dict):
            sent = {}
        fn, session, data = sent.get("fn_index"), sent.get("session_hash"), sent.get("data")
        fn = fn if isinstance(fn, int) else None
        inputs = data if isinstance(data, list) else []
        return {
            "session": session[:64] if isinstance(session, str) else None,
            "fn": fn,
            "name": self._name_of(fn, app),
            "view": next((x for x in inputs if isinstance(x, str) and x in self.views), None),
        }

    def _name_of(self, fn: int | None, app) -> str | None:
        """The function's name, from the same config the page was sent. A number
        alone means nothing after the next deploy renumbers them."""
        if self._names is None:
            try:
                wired = app.get_blocks().config["dependencies"]
            except (AttributeError, KeyError, TypeError):
                return None
            self._names = {d["id"]: d["api_name"] for d in wired if isinstance(d["api_name"], str)}
        return self._names.get(fn)


def _site(referer: str | None) -> str | None:
    """The site a visitor came from: never the page on it, nor a query string."""
    if not referer:
        return None
    try:
        return urlsplit(referer).netloc or None
    except ValueError:
        return None


class RecordVisits:
    """Note each page served and each view drawn, and pass everything through."""

    def __init__(self, app: ASGIApp, recorder: Recorder) -> None:
        self.app = app
        self.recorder = recorder
        self._named = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Three requests are read. Every other one, the 67 files of a page load
        # among them, costs these two comparisons and nothing more.
        method, path = scope["method"], scope["path"]
        # `GET /` only: launching the app asks for `HEAD /` and its startup route
        # before any visitor, and neither is a visit.
        if method == "GET" and path == "/":
            headers = _headers(scope)
            page = {**_seen(headers), "kind": "page", "ref": _site(headers.get("referer")),
                    "dest": headers.get("sec-fetch-dest")}
            if not self._named:
                # Once per run, the names of the headers that reached the app:
                # what the Hub's proxy passes on cannot be seen from outside.
                page["names"] = sorted(headers)
                self._named = True
            self.recorder.note(page)
        elif method == "GET" and path == "/theme.css":
            # The first thing the page's script asks for, which a fetcher that
            # runs no script never does.
            self.recorder.note({**_seen(_headers(scope)), "kind": "browser"})
        elif method == "POST" and path.endswith("/queue/join"):
            await self._draw(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _draw(self, scope: Scope, receive: Receive, send: Send) -> None:
        draw = {**_seen(_headers(scope)), "kind": "draw", "app": scope.get("app"), "body": b""}

        async def copying() -> dict:
            message = await receive()
            if message["type"] == "http.request":
                # One byte past the cap, so a body cut short is known to be cut
                # and is not read as a draw.
                room = _BODY_CAP + 1 - len(draw["body"])
                draw["body"] += message.get("body", b"")[:room]
            return message

        try:
            await self.app(scope, copying, send)
        finally:
            self.recorder.note(draw)


def _headers(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}


def _seen(headers: dict[str, str]) -> dict:
    return {"t": _now(), "host": headers.get("host")}


def recording(app_kwargs: dict, *, views: frozenset[str]) -> dict:
    """``app_kwargs`` with the listener in front, where a folder is named for it."""
    named = os.environ.get(FOLDER_VARIABLE)
    if not named:
        return app_kwargs
    folder = Path(named)
    if not folder.is_dir():
        print(f"\n*** USAGE RECORD OFF: {FOLDER_VARIABLE} names {named}, which is not "
              "a folder here. The app is unaffected; visits are not being recorded. ***\n",
              flush=True)
        return app_kwargs
    recorder = Recorder(folder, views=views)
    # Written at the first flush whether or not anyone visits: proof the folder
    # takes a file, and a mark of each restart.
    recorder.note({"t": _now(), "kind": "start"})
    atexit.register(recorder.close)
    return {
        **app_kwargs,
        "middleware": [Middleware(RecordVisits, recorder=recorder), *app_kwargs["middleware"]],
    }


# One function wired to several controls is numbered by Gradio (`draw_cards_3`).
_WIRING_SUFFIX = re.compile(r"_\d+$")

_COLUMNS = ("Pages", "Visits", "Draws", "Visits that drew")

_LEGEND = """\
Pages: every time the page was served, scanners and link previews included.
Visits: of those, the loads whose script then ran in a browser."""


def _called(line: dict) -> str:
    return _WIRING_SUFFIX.sub("", line["name"] or f"function {line['fn']}")


def _draws_a_view(line: dict) -> bool:
    """One pick by a visitor is several calls: the busy state, the drawing, and
    what tidies up after it. The drawing is the one the app names ``draw_...``.
    The rest are listed apart, so a drawing function renamed out of that pattern
    turns up there instead of vanishing."""
    return _called(line).startswith("draw_")


def _drawn(line: dict) -> str:
    return line["view"] or _called(line)


def _listing(title: str, counts: Counter) -> str:
    wide = max(len(label) for label in counts)
    rows = sorted(counts.items(), key=lambda row: (-row[1], row[0]))
    return "\n".join([title, *(f"  {label:<{wide}}  {n}" for label, n in rows)])


def report(folder: Path, *, by: str) -> str:
    """What a copy of the recorded folder says, counted by day or by week."""
    lines = sorted(
        (json.loads(line) for path in Path(folder).rglob("*.jsonl")
         for line in path.read_text().splitlines()),
        key=lambda line: line["t"],
    )
    events = [line for line in lines if line["kind"] in ("page", "browser", "draw")]
    if not events:
        return f"No usage recorded under {folder}.\n"

    def period(line: dict) -> str:
        day = datetime.fromisoformat(line["t"]).date()
        return str(day - timedelta(days=day.weekday()) if by == "week" else day)

    pages = [line for line in events if line["kind"] == "page"]
    calls = [line for line in events if line["kind"] == "draw"]
    draws = [line for line in calls if _draws_a_view(line)]
    in_period = Counter((period(line), line["kind"]) for line in events)
    head = "Week of" if by == "week" else "Day"
    table = [f"{head:<10}  " + "  ".join(_COLUMNS)]
    for when in sorted({period(line) for line in events}):
        drawn = [line for line in draws if period(line) == when]
        figures = (
            in_period[when, "page"],
            in_period[when, "browser"],
            len(drawn),
            len({line["session"] for line in drawn} - {None}),
        )
        table.append(f"{when}  " + "  ".join(
            f"{n:>{len(column)}}" for n, column in zip(figures, _COLUMNS)
        ))

    last = {line["session"]: _drawn(line) for line in draws if line["session"]}
    listings = [
        ("Address", Counter(line["host"] or "(not said)" for line in pages)),
        ("Came from", Counter(line["ref"] or "(not said)" for line in pages)),
        ("Drawn", Counter(_drawn(line) for line in draws)),
        ("Last thing drawn in a visit", Counter(last.values())),
        ("Other calls", Counter(_called(line) for line in calls if not _draws_a_view(line))),
    ]
    first, final = (datetime.fromisoformat(events[i]["t"]).date() for i in (0, -1))
    parts = [
        f"Usage by {by} (UTC), {first} to {final}",
        "\n".join(table),
        _LEGEND,
        *(_listing(title, counts) for title, counts in listings if counts),
    ]
    dropped = sum(line["n"] for line in lines if line["kind"] == "dropped")
    if dropped:
        parts.append(f"RECORDER DROPPED {dropped} NOTES: the queue was full, "
                     "so these counts are short.")
    return "\n\n".join(parts) + "\n"
