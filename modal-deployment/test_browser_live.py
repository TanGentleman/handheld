"""browser.Rodney against a real rodney + Chrome. Opt in:

    HANDHELD_LIVE=1 ROD_CHROME_BIN=/path/to/chrome uv run pytest -q -k live
"""

import functools
import http.server
import os
import shutil
import struct
import threading

import pytest

from browser import SCREEN_HEIGHT, SCREEN_WIDTH, Rodney

pytestmark = pytest.mark.skipif(
    os.environ.get("HANDHELD_LIVE") != "1" or not shutil.which("rodney"),
    reason="set HANDHELD_LIVE=1 and put rodney on PATH",
)

PAGE = "<title>Live</title><body style='height:5000px'><input id=q></body>"


@pytest.fixture(scope="module")
def page_url(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    (root / "index.html").write_text(PAGE)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/index.html"
    server.shutdown()


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    r = Rodney(home=str(tmp_path_factory.mktemp("rodney-home")))
    r.ensure_started()
    assert r.is_running()
    yield r
    r.stop()


def test_live_open_title_screenshot(browser, page_url):
    assert browser.open_url(page_url)["exit_code"] == 0
    assert browser.get_title()["title"] == "Live"
    png = browser.screenshot()["png"]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    # viewport-sized even though the page is 5000px tall
    assert (w, h) == (SCREEN_WIDTH, SCREEN_HEIGHT)


def test_live_input_and_scroll(browser):
    assert browser.run_command(["input", "#q", "hello"])["exit_code"] == 0
    assert browser.run_js("document.querySelector('#q').value")["result"] == "hello"
    browser.scroll(0, 300)
    assert browser.run_js("window.scrollY")["result"] == "300"
    assert browser.click(5, 5)["exit_code"] == 0
