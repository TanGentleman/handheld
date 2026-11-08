"""Drive one Chromium through the rodney CLI.

No Modal imports: the Modal app and the tests both use this class directly.
Every call returns a plain dict (the shapes the HTTP API returns).
"""

import base64
import os
import subprocess
import tempfile

SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720
DEFAULT_TIMEOUT = 60  # seconds, for the fixed commands
MAX_TIMEOUT = 120  # seconds, cap for /run
TIMEOUT_EXIT_CODE = 124


class Rodney:
    def __init__(self, binary: str = "rodney", home: str | None = None):
        self.binary = binary
        # RODNEY_HOME gives each browser its own state dir (and Chrome profile).
        self.env = None if home is None else {**os.environ, "RODNEY_HOME": home}

    def _run(self, *args: str, timeout: float = DEFAULT_TIMEOUT, text: bool = True):
        # --global so a stray ./.rodney in the working directory is never picked up.
        try:
            return subprocess.run(
                [self.binary, "--global", *args],
                capture_output=True, text=text, env=self.env, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            msg = f"rodney {args[0] if args else ''} timed out after {timeout}s"
            out = "" if text else b""
            return subprocess.CompletedProcess(args, TIMEOUT_EXIT_CODE, out, msg if text else msg.encode())

    def start(self) -> dict:
        r = self._run("start")
        return {"exit_code": r.returncode, "output": r.stdout.strip(), "stderr": r.stderr.strip()}

    def ensure_started(self) -> None:
        if self._run("status").returncode != 0:
            r = self._run("start")
            if r.returncode != 0:
                raise RuntimeError(f"rodney start failed: {r.stderr.strip()}")

    def stop(self) -> dict:
        r = self._run("stop")
        return {"exit_code": r.returncode}

    def status(self) -> dict:
        r = self._run("status")
        return {"exit_code": r.returncode, "output": r.stdout.strip()}

    def screenshot(self) -> dict:
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            # -w/-h capture the viewport; without -h rodney captures the full page,
            # which breaks tap-to-click coordinates and scrolling in the UI.
            r = self._run("screenshot", "-w", str(SCREEN_WIDTH), "-h", str(SCREEN_HEIGHT), path)
            if r.returncode != 0:
                return {"error": r.stderr.strip() or f"rodney screenshot exited {r.returncode}"}
            with open(path, "rb") as f:
                return {"png": f.read()}
        finally:
            os.remove(path)

    def get_url(self) -> dict:
        r = self._run("url")
        return {"url": r.stdout.strip(), "exit_code": r.returncode}

    def get_title(self) -> dict:
        r = self._run("title")
        return {"title": r.stdout.strip(), "exit_code": r.returncode}

    def open_url(self, url: str) -> dict:
        r = self._run("open", url)
        if r.returncode != 0:
            # e.g. someone ran `rodney stop` through /run: start again and retry once
            self._run("start")
            r = self._run("open", url)
        return {"exit_code": r.returncode, "stderr": r.stderr.strip()}

    def run_js(self, expression: str) -> dict:
        r = self._run("js", expression)
        return {"result": r.stdout.strip(), "exit_code": r.returncode, "stderr": r.stderr.strip()}

    def click(self, x: int, y: int) -> dict:
        x, y = int(x), int(y)
        js_code = f"""(function(){{
            var el=document.elementFromPoint({x},{y});
            if(!el) return 'no element';
            el.dispatchEvent(new MouseEvent('mouseover',{{bubbles:true,clientX:{x},clientY:{y}}}));
            el.dispatchEvent(new MouseEvent('mousedown',{{bubbles:true,clientX:{x},clientY:{y}}}));
            el.dispatchEvent(new MouseEvent('mouseup',{{bubbles:true,clientX:{x},clientY:{y}}}));
            el.dispatchEvent(new MouseEvent('click',{{bubbles:true,cancelable:true,clientX:{x},clientY:{y}}}));
            return el.tagName+(el.id?'#'+el.id:'');
        }})()"""
        r = self._run("js", js_code)
        return {"exit_code": r.returncode, "clicked": r.stdout.strip()}

    def scroll(self, dx: int, dy: int) -> dict:
        r = self._run("js", f"window.scrollBy({int(dx)},{int(dy)})")
        return {"exit_code": r.returncode}

    def viewport(self) -> dict:
        r = self._run("js", "JSON.stringify({w:window.innerWidth,h:window.innerHeight})")
        return {"exit_code": r.returncode, "viewport": r.stdout.strip()}

    def run_command(self, args: list[str], timeout: int = 30) -> dict:
        r = self._run(*args, timeout=max(1, min(timeout, MAX_TIMEOUT)), text=False)
        try:
            stdout, is_binary = r.stdout.decode("utf-8"), False
        except UnicodeDecodeError:
            stdout, is_binary = base64.b64encode(r.stdout).decode("ascii"), True
        return {
            "exit_code": r.returncode,
            "stdout": stdout,
            "stderr": r.stderr.decode("utf-8", errors="replace"),
            "is_binary": is_binary,
        }
