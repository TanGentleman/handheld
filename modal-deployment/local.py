"""Run handheld on this machine: the same app and UI, one local Chrome per agent.

    uv run modal-deployment/local.py [--host 127.0.0.1] [--port 8000]

Needs rodney on PATH (go install github.com/simonw/rodney@v0.4.0). Set
ROD_CHROME_BIN to use an installed Chrome; otherwise rodney downloads Chromium.
Set RODNEY_API_TOKENS to choose the login token; otherwise one is generated.
"""

import argparse
import os
import secrets
import shutil
import sys
import tempfile
import threading
from pathlib import Path

from browser import Rodney


class LocalBackend:
    """Agents are rodney sessions on this machine, each with its own RODNEY_HOME."""

    def __init__(self, root: str, binary: str = "rodney"):
        self.root = Path(root)
        self.binary = binary
        self.registry: dict = {}
        self._browsers: dict[str, Rodney] = {}
        self._lock = threading.Lock()

    def agent(self, agent_id: str) -> Rodney:
        with self._lock:
            browser = self._browsers.get(agent_id)
            if browser is None:
                browser = Rodney(binary=self.binary, home=str(self.root / agent_id))
                browser.ensure_started()
                self._browsers[agent_id] = browser
            return browser

    def forget(self, agent_id: str) -> None:
        with self._lock:
            browser = self._browsers.pop(agent_id, None)
        if browser is not None:
            browser.stop()

    def close(self) -> None:
        for agent_id in list(self._browsers):
            self.forget(agent_id)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to reach it from your phone")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if shutil.which("rodney") is None:
        sys.exit("rodney not found on PATH: go install github.com/simonw/rodney@v0.4.0")

    if not os.environ.get("RODNEY_API_TOKENS", "").strip(", "):
        token = secrets.token_urlsafe(24)
        os.environ["RODNEY_API_TOKENS"] = token
        print(f"login token: {token}")
    os.environ.setdefault("RODNEY_COOKIE_SECRET", secrets.token_hex(32))

    import uvicorn

    from deploy import create_app

    root = tempfile.mkdtemp(prefix="handheld-")
    backend = LocalBackend(root)
    # Plain HTTP: a Secure cookie would be dropped off localhost, so it is off here.
    app = create_app(backend=backend, secure_cookie=False)
    print(f"open http://{args.host}:{args.port}/ (browser state in {root})")
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        backend.close()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
