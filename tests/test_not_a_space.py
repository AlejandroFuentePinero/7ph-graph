"""The page is not told it runs on a Space (issue #313).

Gradio reads the Space's name from the environment and puts it in the page's config.
A page that finds one there, and is not framed, asks huggingface.co about the Space as
an anonymous visitor so it can draw Hugging Face's badge. This Space is protected, so
the Hub refuses, the badge code reads a field of the answer it did not get, and every
load logged a failed request and an exception. The badge's code also asked Google
Fonts for two stylesheets on every load, for type the page never drew (issue #321).
Nobody reaches the app through the Space's own page: visitors come by the custom
domain, where a Space badge would be wrong even if it drew.

Launched here as the Space launches it, with the two variables a Space sets, over the
suite's small snapshot. Asked of a browser because the request is the page's own: no
endpoint of the app makes it.

Skipped where Chromium is missing, as ``tests/test_graph_desktop.py`` is.
"""

import pytest

from graph7ph.app import build_app, launch_kwargs
from graph7ph.build import build_graph
from graph7ph.models import load_snapshot

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright


def test_a_page_served_from_a_space_asks_no_other_host_for_anything(
    tmp_path, snapshot_dir, monkeypatch
):
    monkeypatch.setenv("SYSTEM", "spaces")
    monkeypatch.setenv("SPACE_ID", "someone/some-space")
    # On a Space, Gradio also starts a watcher that reloads the app when its files
    # change, and it needs the `spaces` package only a Space installs. It is not
    # part of what is under test, and without that package its thread dies.
    monkeypatch.setattr("gradio.http_server.watchfn_spaces", lambda reloader: None)
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    demo = build_app(artifact)
    _, url, _ = demo.launch(prevent_thread_lock=True, **launch_kwargs())
    try:
        with sync_playwright() as play:
            try:
                engine = play.chromium.launch()
            except Exception as missing:  # noqa: BLE001 - any launch failure is the same skip
                pytest.skip(f"no Chromium to load the page in: {missing}")
            page = engine.new_page()
            # Nothing leaves the machine: a request for another host is noted and
            # refused, so the test neither needs the network nor touches the Hub.
            elsewhere = []

            def keep_it_local(route):
                if route.request.url.startswith(url):
                    route.continue_()
                else:
                    elsewhere.append(route.request.url)
                    route.abort()

            page.route("**/*", keep_it_local)
            thrown = []
            page.on("pageerror", lambda error: thrown.append(str(error)))
            page.goto(url, wait_until="networkidle")
            page.get_by_role("tab", name="Metagame", exact=True).wait_for()

            assert page.request.get(url + "config").json()["space_id"] is None
            # Nothing at all, and not only nothing of the Hub's: the badge also brought
            # two stylesheets from Google Fonts with it, for type the page never drew
            # (issue #321). With it gone the page asks no host but the app's own.
            assert elsewhere == []
            # Drawn is not enough: the page still gets an answer from the app.
            with page.expect_response(lambda response: "/queue/join" in response.url) as asked:
                # The chip, as a reader takes it: its radio mark is not drawn (#328).
                page.get_by_text("Top 75%", exact=True).click()
            assert asked.value.ok
            assert thrown == []
            engine.close()
    finally:
        demo.close()
