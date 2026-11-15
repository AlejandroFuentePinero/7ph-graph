"""The check a deploy ends on: is the app drawn at the address (issue #315).

On 2026-11-15 a deploy left the public address answering ``200`` with a login form
over "Failed to fetch" (#307), and nothing noticed until someone loaded the page.
``scripts/check_live_page.py`` is that someone. These run it as the deploy script
does, as a command given addresses, against small local pages that are each broken
in one way, and read its exit status and what it printed.

The pages are stand-ins and not a Gradio app: what is under test is what the check
makes of a page, and the one thing it needs from Gradio's markup is that a tab is an
element with the ``tab`` role.

Skipped where Chromium is missing, as ``tests/test_graph_desktop.py`` is.
"""

import http.server
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

CHECK = Path(__file__).resolve().parents[1] / "scripts" / "check_live_page.py"

TABS = "<div role='tablist'><button role='tab'>Meta</button><button role='tab'>Cards</button></div>"

# What the public address showed during #307: a page that answers 200, and on it
# Gradio's login form over the reason it could not go on.
LOGIN_FORM = (
    "<form><label>username<input name='username'></label>"
    "<label>password<input type='password'></label><button>Login</button></form>"
    "<p>Failed to fetch</p>"
)

# The live page did this once on every load until #327, to commit the dark theme:
# it sent itself to its own address with a query, which abandons whatever it was
# still fetching. `/slow.js` is slow enough to be caught mid-flight.
REDIRECTS_ITSELF_ONCE = (
    "<script src='/slow.js' async></script><script>"
    "if (!location.search) location.replace('?__theme=dark');</script>" + TABS
)


def _serve(pages: dict[str, str]):
    """Serve the given bodies by path, 404 for anything else."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # a passing test should say nothing

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/slow.js":
                time.sleep(1)
            body = pages.get(path)
            try:
                if body is None:
                    self.send_error(404)
                    return
                script = path.endswith(".js")
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript" if script else "text/html")
                self.end_headers()
                self.wfile.write((body if script else f"<html><body>{body}</body></html>").encode())
            except (BrokenPipeError, ConnectionResetError):
                pass  # the page left before the answer did, which is the point

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


@pytest.fixture
def check():
    """Run the check on a page with the given body, as the deploy script runs it."""
    servers = []

    def run(body: str, **also: str) -> subprocess.CompletedProcess:
        server, url = _serve({"/": body, "/slow.js": "", **also})
        servers.append(server)
        result = subprocess.run(
            [sys.executable, str(CHECK), url], capture_output=True, text=True
        )
        if result.returncode == 2:
            pytest.skip(f"no Chromium to load the page in: {result.stderr.strip()}")
        result.url = url
        return result

    yield run
    for server in servers:
        server.shutdown()
        server.server_close()


def test_a_page_with_its_tabs_drawn_passes(check):
    result = check(TABS)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.url in result.stdout


def test_a_page_that_answers_200_with_a_login_form_fails(check):
    """The outage itself. The status is fine, so the status is not the test."""
    result = check(LOGIN_FORM)

    assert result.returncode == 1
    # What was seen, in the page's own words, so the banner needs no second look.
    assert "no tabs" in result.stdout
    assert "Failed to fetch" in result.stdout
    assert result.url in result.stdout


def test_a_drawn_page_whose_script_threw_fails(check):
    result = check(TABS + "<script>throw new Error('badge fell over')</script>")

    assert result.returncode == 1
    assert "badge fell over" in result.stdout


def test_a_drawn_page_with_a_request_that_was_refused_fails(check):
    result = check(TABS + "<img src='/missing.png'>")

    assert result.returncode == 1
    assert "404" in result.stdout
    assert "/missing.png" in result.stdout


def test_a_request_the_page_abandoned_by_reloading_itself_is_not_a_failure(check):
    result = check(REDIRECTS_ITSELF_ONCE)

    assert result.returncode == 0, result.stdout + result.stderr


def test_every_address_is_checked_and_one_broken_one_fails_the_lot(check):
    """A Space answers at two addresses behind different proxies, and #307 broke one."""
    good = check(TABS)
    server, broken = _serve({"/": TABS + "<script>throw new Error('only here')</script>"})
    try:
        result = subprocess.run(
            [sys.executable, str(CHECK), good.url, broken], capture_output=True, text=True
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 1
    assert f"drawn: {good.url}" in result.stdout
    assert f"BROKEN: {broken}" in result.stdout
