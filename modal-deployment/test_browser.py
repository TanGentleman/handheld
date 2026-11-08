"""browser.Rodney against a fake rodney executable (no Chrome needed).

The fake logs each argv as a JSON line and answers like rodney v0.4.0.
"""

import json
import os
import stat
import sys
import textwrap

import pytest

from browser import MAX_TIMEOUT, TIMEOUT_EXIT_CODE, Rodney

FAKE = textwrap.dedent(
    """\
    #!PYTHON
    import json, os, sys, time
    args = sys.argv[1:]
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(json.dumps({"args": args, "home": os.environ.get("RODNEY_HOME")}) + "\\n")
    cmd = [a for a in args if a != "--global"]
    fail = os.environ.get("FAKE_FAIL", "").split(",")
    if cmd[0] in fail:
        print(cmd[0] + " failed", file=sys.stderr)
        sys.exit(2)
    if cmd[0] == "screenshot":
        open(cmd[-1], "wb").write(b"\\x89PNG fake")
        print(cmd[-1])
    elif cmd[0] == "title":
        print("Fake Title")
    elif cmd[0] == "url":
        print("https://example.com/")
    elif cmd[0] == "js":
        print("js-result")
    elif cmd[0] == "sleep":
        time.sleep(float(cmd[1]))
    elif cmd[0] == "pdf":
        sys.stdout.buffer.write(b"\\xff\\xfe binary")
    """
).replace("PYTHON", sys.executable)


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    binary = tmp_path / "rodney"
    binary.write_text(FAKE)
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("FAKE_FAIL", raising=False)

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    return Rodney(binary=str(binary)), calls


def test_every_call_is_global(fake):
    r, calls = fake
    r.status(); r.get_url(); r.get_title(); r.run_js("1"); r.scroll(0, 1)
    assert all(c["args"][0] == "--global" for c in calls())


def test_screenshot_is_viewport_sized_and_cleaned_up(fake):
    r, calls = fake
    out = r.screenshot()
    assert out == {"png": b"\x89PNG fake"}
    args = calls()[0]["args"]
    assert args[1:6] == ["screenshot", "-w", "1280", "-h", "720"]
    assert not os.path.exists(args[-1])


def test_screenshot_error(fake, monkeypatch):
    r, _ = fake
    monkeypatch.setenv("FAKE_FAIL", "screenshot")
    assert r.screenshot() == {"error": "screenshot failed"}


def test_url_title_js(fake):
    r, _ = fake
    assert r.get_url() == {"url": "https://example.com/", "exit_code": 0}
    assert r.get_title() == {"title": "Fake Title", "exit_code": 0}
    assert r.run_js("1+1") == {"result": "js-result", "exit_code": 0, "stderr": ""}


def test_open_restarts_and_retries_once(fake, monkeypatch):
    r, calls = fake
    monkeypatch.setenv("FAKE_FAIL", "open")
    out = r.open_url("https://example.com")
    assert out["exit_code"] == 2
    assert [c["args"][1] for c in calls()] == ["open", "start", "open"]


def test_click_coordinates_are_ints_in_js(fake):
    r, calls = fake
    assert r.click(10, 20)["exit_code"] == 0
    js = calls()[0]["args"][2]
    assert "elementFromPoint(10,20)" in js
    with pytest.raises(ValueError):
        r.click("1);alert(1", 2)


def test_scroll(fake):
    r, calls = fake
    r.scroll(-5, 500)
    assert calls()[0]["args"][1:] == ["js", "window.scrollBy(-5,500)"]


def test_run_command_passes_args_through(fake):
    r, calls = fake
    out = r.run_command(["input", "#q", "hello world"])
    assert out == {"exit_code": 0, "stdout": "", "stderr": "", "is_binary": False}
    assert calls()[0]["args"] == ["--global", "input", "#q", "hello world"]


def test_run_command_binary_stdout_is_base64(fake):
    r, _ = fake
    out = r.run_command(["pdf", "-"])
    assert out["is_binary"] is True
    assert out["stdout"] == "//4gYmluYXJ5"


def test_run_command_timeout_returns_124(fake):
    r, _ = fake
    out = r.run_command(["sleep", "5"], timeout=1)
    assert out["exit_code"] == TIMEOUT_EXIT_CODE
    assert "timed out" in out["stderr"]


def test_run_command_timeout_is_capped(fake, monkeypatch):
    r, _ = fake
    seen = {}
    real_run = r._run

    def spy(*args, timeout, text):
        seen["timeout"] = timeout
        return real_run(*args, timeout=1, text=text)

    monkeypatch.setattr(r, "_run", spy)
    r.run_command(["status"], timeout=10_000)
    assert seen["timeout"] == MAX_TIMEOUT


def test_home_sets_rodney_home(fake, tmp_path):
    r, calls = fake
    scoped = Rodney(binary=r.binary, home=str(tmp_path / "agent-a"))
    scoped.status()
    r.status()
    assert calls()[0]["home"] == str(tmp_path / "agent-a")
    assert calls()[1]["home"] in (None, os.environ.get("RODNEY_HOME"))
