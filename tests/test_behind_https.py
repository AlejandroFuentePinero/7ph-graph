"""The page as a visitor to the public address gets it: over HTTPS, behind a proxy.

Issue #307: the deploy of Gradio 6 took the public address down while the Space's own
address, the suite and a browser check of the local app were all fine. Gradio 6 writes
the app's own address into the page, and takes the scheme from what the proxy in
front reports. The custom domain's proxy ends the TLS connection and does not report
it, so the page at ``https://www.7phgraph.com`` was told its address was
``http://www.7phgraph.com``, asked for its config there, and the browser refused the
request as mixed content. What a visitor saw was a login form over "Failed to fetch".

None of that can happen on ``http://127.0.0.1``, which is where every other browser
test loads the page, so this one stands the same arrangement up: a proxy that ends
TLS and says nothing about it, in front of the app, reached by a name that is not
this machine's own (a browser lets a page on a loopback address fetch anything).

Only Gradio's server-side rendering writes that address into the page, and its
renderer is a Node process in front of the app that nothing here can correct, so the
app is launched with server-side rendering off. The Space asks for it on
(``GRADIO_SSR_MODE``), which is the environment this test launches in.

Skipped where a part of that is missing (playwright or its browser, as
``tests/test_page_style.py``; ``openssl`` for the throwaway certificate).
"""

import shutil
import socket
import ssl
import subprocess
import threading
from urllib.parse import urlsplit

import gradio as gr
import pytest

from graph7ph.app import launch_kwargs

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

# A public name for the app, resolved to this machine inside the test's browser only.
PUBLIC_HOST = "explorer.example.test"


def _pipe(source: socket.socket, sink: socket.socket) -> None:
    try:
        while data := source.recv(65536):
            sink.sendall(data)
    except OSError:
        pass
    finally:
        for end in (source, sink):
            try:
                end.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _end_tls_in_front_of(port: int, certificate: str, key: str) -> socket.socket:
    """A proxy that ends TLS and passes the bytes on unchanged: no forwarded scheme."""
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, key)
    listener = socket.create_server(("127.0.0.1", 0))

    def connect(raw: socket.socket) -> None:
        try:
            visitor = tls.wrap_socket(raw, server_side=True)
            app = socket.create_connection(("127.0.0.1", port))
        except OSError:
            raw.close()
            return
        threading.Thread(target=_pipe, args=(visitor, app), daemon=True).start()
        _pipe(app, visitor)

    def accept() -> None:
        while True:
            try:
                raw, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=connect, args=(raw,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    return listener


def test_the_page_draws_at_an_https_address_whose_proxy_does_not_report_the_scheme(
    tmp_path, monkeypatch
):
    if not shutil.which("openssl"):
        pytest.skip("no openssl to make a throwaway certificate with")
    certificate, key = str(tmp_path / "cert.pem"), str(tmp_path / "key.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", f"/CN={PUBLIC_HOST}", "-keyout", key, "-out", certificate],
        check=True, capture_output=True,
    )

    with gr.Blocks() as demo:
        gr.Markdown("# The page title")
        gr.HTML("<div class='drawn'>drawn</div>")
        answer = gr.Textbox(label="Answer")
        gr.Button("Ask").click(lambda: "answered", outputs=answer)
    # As the Space launches it: the settings every real launch passes, where the
    # environment asks for server-side rendering.
    monkeypatch.setenv("GRADIO_SSR_MODE", "true")
    _, url, _ = demo.launch(prevent_thread_lock=True, **launch_kwargs())
    proxy = None
    try:
        proxy = _end_tls_in_front_of(urlsplit(url).port, certificate, key)
        with sync_playwright() as play:
            try:
                engine = play.chromium.launch(
                    args=[f"--host-resolver-rules=MAP {PUBLIC_HOST} 127.0.0.1"]
                )
            except Exception as missing:  # noqa: BLE001 - any launch failure is the same skip
                pytest.skip(f"no Chromium to load the page in: {missing}")
            # The proxy names the host it was reached at, and only that.
            public = f"{PUBLIC_HOST}:{proxy.getsockname()[1]}"
            page = engine.new_context(
                ignore_https_errors=True, extra_http_headers={"X-Forwarded-Host": public}
            ).new_page()
            refused = []
            page.on("console", lambda message: "Mixed Content" in message.text
                    and refused.append(message.text))
            page.goto(f"https://{public}/", wait_until="networkidle")

            assert page.locator(".drawn").count() == 1
            assert "Failed to fetch" not in page.inner_text("body")
            # Drawn is not enough: a result is asked for over the same address.
            page.get_by_role("button", name="Ask").click()
            page.wait_for_function(
                "() => document.querySelector('textarea, input[type=text]').value === 'answered'",
                timeout=10_000,
            )
            assert refused == []
            engine.close()
    finally:
        if proxy:
            proxy.close()
        demo.close()
