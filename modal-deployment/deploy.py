"""
Handheld on Modal — multi-agent browser automation accessible from any device.

Deploys:
  1. BrowserAgent (modal.Cls) — one Chromium instance per agent container
  2. Orchestrator (FastAPI ASGI) — agent lifecycle + proxied browser commands

Usage:
  # Development (hot-reload)
  modal serve deploy.py

  # Production (persistent URL)
  modal deploy deploy.py

Prerequisites:
  - modal secret create rodney-auth RODNEY_API_TOKENS=tok1,tok2 RODNEY_COOKIE_SECRET=hex...
"""

import hashlib
import hmac
import random
import string
from pathlib import Path

import modal

from browser import Rodney

# Directory containing this file (used for Modal image mounts and local pytest).
_DEPLOY_ROOT = Path(__file__).resolve().parent
# In the container, `web/` is copied here so HTML can be read at runtime (see `rodney_image`).
_HANDHELD_WEB_MOUNT = "/opt/handheld-web"

# --- Image ---

# rodney v0.4.0 needs Go >= 1.24.7. Bump both deliberately; rodney's CLI changes often.
GO_VERSION = "1.26.10"
RODNEY_VERSION = "v0.4.0"

rodney_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "chromium",
        "fonts-liberation",
        "fonts-noto-color-emoji",
        "curl",
        "ca-certificates",
    )
    .run_commands(
        f"curl -fsSL https://go.dev/dl/go{GO_VERSION}.linux-amd64.tar.gz | tar -C /usr/local -xz",
    )
    .run_commands(
        "GOPATH=/root/go CGO_ENABLED=0 /usr/local/go/bin/go install -ldflags='-s -w' "
        f"github.com/simonw/rodney@{RODNEY_VERSION}",
    )
    .uv_pip_install("fastapi[standard]==0.162.0", "itsdangerous==2.2.0")
    .add_local_dir(
        _DEPLOY_ROOT / "web",
        remote_path=_HANDHELD_WEB_MOUNT,
        copy=True,
    )
    .env(
        {
            "ROD_CHROME_BIN": "/usr/bin/chromium",
            "RODNEY_HOME": "/root/.rodney",
            "PATH": "/root/go/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HANDHELD_WEB_DIR": _HANDHELD_WEB_MOUNT,
        }
    )
    .add_local_python_source("browser")
)

app = modal.App("rodney", image=rodney_image)

# --- Agent registry (shared state across containers) ---

agent_registry = modal.Dict.from_name("rodney-agents", create_if_missing=True)

DEFAULT_AGENT_ID = "default"
AGENT_ID_LENGTH = 6
AGENT_ID_CHARS = string.ascii_lowercase + string.digits


def _generate_agent_id() -> str:
    return "".join(random.choices(AGENT_ID_CHARS, k=AGENT_ID_LENGTH))


# --- BrowserAgent (one container per agent) ---


@app.cls(
    timeout=86400,
    scaledown_window=300,
)
class BrowserAgent:
    """Thin Modal wrapper: the rodney calls live in browser.Rodney."""

    # Each distinct agent_id gets its own container / Chromium (Modal parametrized cls).
    agent_id: str = modal.parameter()

    @modal.enter()
    def start_chrome(self):
        self.browser = Rodney()
        self.browser.ensure_started()

    @modal.exit()
    def stop_chrome(self):
        self.browser.stop()

    @modal.method()
    def status(self) -> dict:
        return self.browser.status()

    @modal.method()
    def screenshot(self) -> dict:
        return self.browser.screenshot()

    @modal.method()
    def get_url(self) -> dict:
        return self.browser.get_url()

    @modal.method()
    def get_title(self) -> dict:
        return self.browser.get_title()

    @modal.method()
    def open_url(self, url: str) -> dict:
        return self.browser.open_url(url)

    @modal.method()
    def run_js(self, expression: str) -> dict:
        return self.browser.run_js(expression)

    @modal.method()
    def click(self, x: int, y: int) -> dict:
        return self.browser.click(x, y)

    @modal.method()
    def scroll(self, dx: int, dy: int) -> dict:
        return self.browser.scroll(dx, dy)

    @modal.method()
    def viewport(self) -> dict:
        return self.browser.viewport()

    @modal.method()
    def run_command(self, args: list[str], timeout: int = 30) -> dict:
        return self.browser.run_command(args, timeout)


# HTML templates: `web/login.html`, `web/dashboard.html`, `web/agent.html` (loaded in `create_app`).

# --- App factory (importable by tests) ---


def create_app():
    import json
    import os
    from datetime import datetime, timezone

    from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
    from fastapi.responses import HTMLResponse, RedirectResponse
    from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
    from pydantic import BaseModel

    web_dir_env = os.environ.get("HANDHELD_WEB_DIR")
    web_dir = Path(web_dir_env) if web_dir_env else _DEPLOY_ROOT / "web"
    login_tpl = string.Template((web_dir / "login.html").read_text(encoding="utf-8"))
    agent_tpl = string.Template((web_dir / "agent.html").read_text(encoding="utf-8"))
    dashboard_html = (web_dir / "dashboard.html").read_text(encoding="utf-8")

    VALID_TOKENS = {t.strip() for t in os.environ.get("RODNEY_API_TOKENS", "").split(",") if t.strip()}
    if not VALID_TOKENS:
        raise RuntimeError("RODNEY_API_TOKENS must contain at least one non-empty token")
    cookie_secret = os.environ.get("RODNEY_COOKIE_SECRET", "")
    if not cookie_secret:
        raise RuntimeError("RODNEY_COOKIE_SECRET must be set")
    cookie_signer = URLSafeTimedSerializer(cookie_secret)
    COOKIE_NAME = "rodney_session"
    COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days

    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    VALID_DIGESTS = {_digest(t) for t in VALID_TOKENS}

    def _digest_is_valid(candidate: str) -> bool:
        """Constant-time check against all valid token digests to prevent timing attacks."""
        candidate_b = candidate.encode("utf-8")
        return any(hmac.compare_digest(candidate_b, d.encode("utf-8")) for d in VALID_DIGESTS)

    def _token_is_valid(candidate: str) -> bool:
        return _digest_is_valid(_digest(candidate))

    def require_auth(request: Request) -> str:
        # Bearer token
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if _token_is_valid(token):
                return token

        # Signed cookie
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie:
            try:
                digest = cookie_signer.loads(cookie, max_age=COOKIE_MAX_AGE)
                if isinstance(digest, str) and _digest_is_valid(digest):
                    return digest
            except (BadSignature, SignatureExpired):
                pass

        raise HTTPException(status_code=401, detail="Unauthorized")

    web_app = FastAPI(title="Handheld", docs_url="/docs")

    # --- Helper: get or create agent instance ---

    def _get_agent(agent_id: str) -> BrowserAgent:
        """Look up an agent by ID. Raises 404 if not registered."""
        try:
            agent_registry[agent_id]
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        return BrowserAgent(agent_id=agent_id)

    def _get_or_create_default() -> str:
        """Ensure the default agent exists, return its ID."""
        try:
            agent_registry[DEFAULT_AGENT_ID]
        except KeyError:
            agent_registry[DEFAULT_AGENT_ID] = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "purpose": "default",
                "status": "active",
            }
        return DEFAULT_AGENT_ID

    # --- UI routes ---

    @web_app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        try:
            require_auth(request)
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)
        return HTMLResponse(dashboard_html)

    @web_app.get("/agents/{agent_id}/ui", response_class=HTMLResponse)
    def agent_ui(agent_id: str, request: Request):
        try:
            require_auth(request)
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)
        _get_agent(agent_id)  # 404 if not found
        return HTMLResponse(agent_tpl.substitute(agent_id=agent_id))

    # --- Auth routes (public) ---

    @web_app.get("/login", response_class=HTMLResponse)
    def login_page():
        return login_tpl.substitute(error="")

    @web_app.post("/login")
    def login_submit(token: str = Form(...)):
        if not _token_is_valid(token):
            return HTMLResponse(
                login_tpl.substitute(error='<p class="error">invalid token</p>'),
                status_code=401,
            )
        # The cookie carries a digest, not the token itself (itsdangerous signs but does not encrypt).
        signed = cookie_signer.dumps(_digest(token))
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            signed,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=True,  # the browser talks HTTPS to Modal, even though the ASGI app sees HTTP
        )
        return response

    @web_app.get("/logout")
    def logout():
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie(COOKIE_NAME, httponly=True, samesite="lax", secure=True)
        return response

    # --- Agent lifecycle endpoints ---

    class CreateAgentRequest(BaseModel):
        purpose: str = "browser agent"

    @web_app.post("/agents")
    def create_agent(
        body: CreateAgentRequest = CreateAgentRequest(),
        _token: str = Depends(require_auth),
    ):
        agent_id = _generate_agent_id()
        agent_registry[agent_id] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "purpose": body.purpose,
            "status": "active",
        }
        # Warm this agent's container (distinct pool per agent_id)
        agent = BrowserAgent(agent_id=agent_id)
        try:
            agent.status.remote()
        except Exception:
            pass
        return {"agent_id": agent_id}

    @web_app.get("/agents")
    def list_agents(_token: str = Depends(require_auth)):
        agents = []
        for key in list(agent_registry.keys()):
            info = agent_registry[key]
            agents.append({"id": key, **info})
        return agents

    @web_app.get("/agents/summary")
    def agents_summary(_token: str = Depends(require_auth)):
        """Lightweight summary of all agents for mobile polling."""
        agents = []
        for key in list(agent_registry.keys()):
            info = agent_registry[key]
            agents.append({
                "id": key,
                "status": info.get("status", "unknown"),
                "purpose": info.get("purpose", ""),
                "created_at": info.get("created_at", ""),
            })
        return agents

    @web_app.get("/agents/{agent_id}")
    def get_agent_info(agent_id: str, _token: str = Depends(require_auth)):
        try:
            info = agent_registry[agent_id]
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        return {"id": agent_id, **info}

    @web_app.delete("/agents/{agent_id}")
    def delete_agent(agent_id: str, _token: str = Depends(require_auth)):
        try:
            del agent_registry[agent_id]
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        return {"deleted": agent_id}

    # --- Scoped agent endpoints ---

    class RodneyCommand(BaseModel):
        args: list[str]
        timeout: int = 30

    @web_app.post("/agents/{agent_id}/run")
    def agent_run(agent_id: str, cmd: RodneyCommand, _token: str = Depends(require_auth)):
        agent = _get_agent(agent_id)
        if not cmd.args:
            raise HTTPException(status_code=422, detail="args must not be empty")
        return agent.run_command.remote(cmd.args, cmd.timeout)

    @web_app.get("/agents/{agent_id}/status")
    def agent_status(agent_id: str, _token: str = Depends(require_auth)):
        agent = _get_agent(agent_id)
        return agent.status.remote()

    @web_app.get("/agents/{agent_id}/screenshot")
    def agent_screenshot(agent_id: str, _token: str = Depends(require_auth)):
        agent = _get_agent(agent_id)
        result = agent.screenshot.remote()
        if "error" in result:
            return Response(
                content=json.dumps({"error": result["error"]}),
                media_type="application/json",
                status_code=500,
            )
        return Response(content=result["png"], media_type="image/png")

    @web_app.get("/agents/{agent_id}/url")
    def agent_url(agent_id: str, _token: str = Depends(require_auth)):
        agent = _get_agent(agent_id)
        return agent.get_url.remote()

    @web_app.get("/agents/{agent_id}/title")
    def agent_title(agent_id: str, _token: str = Depends(require_auth)):
        agent = _get_agent(agent_id)
        return agent.get_title.remote()

    @web_app.post("/agents/{agent_id}/open")
    def agent_open(agent_id: str, url: str, _token: str = Depends(require_auth)):
        agent = _get_agent(agent_id)
        return agent.open_url.remote(url)

    @web_app.post("/agents/{agent_id}/js")
    def agent_js(agent_id: str, expression: str, _token: str = Depends(require_auth)):
        agent = _get_agent(agent_id)
        return agent.run_js.remote(expression)

    @web_app.post("/agents/{agent_id}/click")
    def agent_click(agent_id: str, x: int, y: int, _token: str = Depends(require_auth)):
        agent = _get_agent(agent_id)
        return agent.click.remote(x, y)

    @web_app.post("/agents/{agent_id}/scroll")
    def agent_scroll(agent_id: str, dx: int = 0, dy: int = 0, _token: str = Depends(require_auth)):
        agent = _get_agent(agent_id)
        return agent.scroll.remote(dx, dy)

    @web_app.get("/agents/{agent_id}/viewport")
    def agent_viewport(agent_id: str, _token: str = Depends(require_auth)):
        agent = _get_agent(agent_id)
        return agent.viewport.remote()

    # --- Flat endpoints (aliases to default agent) ---

    @web_app.post("/run")
    def run_command(cmd: RodneyCommand, _token: str = Depends(require_auth)):
        aid = _get_or_create_default()
        agent = _get_agent(aid)
        if not cmd.args:
            raise HTTPException(status_code=422, detail="args must not be empty")
        return agent.run_command.remote(cmd.args, cmd.timeout)

    @web_app.get("/status")
    def status(_token: str = Depends(require_auth)):
        aid = _get_or_create_default()
        return _get_agent(aid).status.remote()

    @web_app.get("/screenshot")
    def screenshot(_token: str = Depends(require_auth)):
        aid = _get_or_create_default()
        agent = _get_agent(aid)
        result = agent.screenshot.remote()
        if "error" in result:
            return Response(
                content=json.dumps({"error": result["error"]}),
                media_type="application/json",
                status_code=500,
            )
        return Response(content=result["png"], media_type="image/png")

    @web_app.get("/url")
    def get_url(_token: str = Depends(require_auth)):
        aid = _get_or_create_default()
        return _get_agent(aid).get_url.remote()

    @web_app.get("/title")
    def title(_token: str = Depends(require_auth)):
        aid = _get_or_create_default()
        return _get_agent(aid).get_title.remote()

    @web_app.post("/open")
    def open_url(url: str, _token: str = Depends(require_auth)):
        aid = _get_or_create_default()
        return _get_agent(aid).open_url.remote(url)

    @web_app.post("/js")
    def run_js(expression: str, _token: str = Depends(require_auth)):
        aid = _get_or_create_default()
        return _get_agent(aid).run_js.remote(expression)

    @web_app.post("/click")
    def click_at(x: int, y: int, _token: str = Depends(require_auth)):
        aid = _get_or_create_default()
        return _get_agent(aid).click.remote(x, y)

    @web_app.post("/scroll")
    def scroll(dx: int = 0, dy: int = 0, _token: str = Depends(require_auth)):
        aid = _get_or_create_default()
        return _get_agent(aid).scroll.remote(dx, dy)

    @web_app.get("/viewport")
    def viewport(_token: str = Depends(require_auth)):
        aid = _get_or_create_default()
        return _get_agent(aid).viewport.remote()

    return web_app


# --- Modal entry point ---


@app.function(
    secrets=[
        modal.Secret.from_name(
            "rodney-auth",
            required_keys=["RODNEY_API_TOKENS", "RODNEY_COOKIE_SECRET"],
        ),
    ],
    timeout=86400,
    scaledown_window=300,
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def api():
    return create_app()
