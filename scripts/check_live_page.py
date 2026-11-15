"""Load a deployed page in a headless browser and say whether the app is drawn.

Usage: check_live_page.py <address> [<address> ...]

What ``scripts/deploy_space.sh`` ends on (issue #315). A deploy can leave an address
answering ``200`` with nothing behind it: on 2026-11-15 the public one showed a login
form over "Failed to fetch" while the suite was green and the Space was running
(#307). So the status is not the test. An address passes when a browser that loads
it finds the app's tabs on screen, every request answered, and no error thrown.

Exits 0 when every address passes, 1 when one does not (saying what was seen at
it), and 2 when there is no browser to look with.
"""

import sys

from playwright.sync_api import Error, sync_playwright

# Long enough for a Space that has only just started to send a megabyte of page.
LOAD_MS = 120_000
# Once the page has gone quiet the tabs are either there or a moment away.
TABS_MS = 10_000


def problems_at(browser, address: str) -> list[str]:
    """What is wrong with the page at ``address``, as a reader would be told it."""
    refused, failed, thrown = [], [], []
    page = browser.new_page()
    page.on("response", lambda response: response.status >= 400
            and refused.append(f"{response.status} from {response.url}"))
    # A request the page abandoned is not one that failed. Until #327 the app
    # reloaded itself once on arrival to commit its dark theme, which dropped
    # whatever was in flight, and a page that leaves for any reason does the same.
    page.on("requestfailed", lambda request: request.failure != "net::ERR_ABORTED"
            and failed.append(f"{request.failure} for {request.url}"))
    page.on("pageerror", lambda error: thrown.append(str(error)))
    found = []
    try:
        page.goto(address, wait_until="networkidle", timeout=LOAD_MS)
        try:
            page.locator("[role=tab]").first.wait_for(timeout=TABS_MS)
        except Error:
            shown = " ".join(page.inner_text("body").split())[:300]
            found.append(f"no tabs were drawn. The page shows: {shown or '(nothing)'}")
    except Error as error:
        found.append(f"the page did not load: {str(error).splitlines()[0]}")
    finally:
        page.close()
    found += [f"a request was refused: {line}" for line in refused]
    found += [f"a request failed: {line}" for line in failed]
    found += [f"the page threw: {line}" for line in thrown]
    # Said once each: a page that reloads itself makes most of its mistakes twice.
    return list(dict.fromkeys(found))


def main(addresses: list[str]) -> int:
    if not addresses:
        print(__doc__, file=sys.stderr)
        return 2
    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Error as missing:
            print(f"No Chromium to load the page in: {str(missing).splitlines()[0]}\n"
                  "Install it with 'uv run playwright install chromium'.", file=sys.stderr)
            return 2
        broken = False
        for address in addresses:
            found = problems_at(browser, address)
            broken = broken or bool(found)
            print(f"{'BROKEN' if found else 'drawn'}: {address}")
            for line in found:
                print(f"  {line}")
        browser.close()
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
