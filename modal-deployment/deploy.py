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

import hmac
import random
import string
import subprocess
from pathlib import Path

import modal

# Directory containing this file (used for Modal image mounts and local pytest).
_DEPLOY_ROOT = Path(__file__).resolve().parent
# In the container, `web/` is copied here so HTML can be read at runtime (see `rodney_image`).
_HANDHELD_WEB_MOUNT = "/opt/handheld-web"

# --- Image ---

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
        "curl -fsSL https://go.dev/dl/go1.24.4.linux-amd64.tar.gz | tar -C /usr/local -xz",
    )
    .run_commands(
        "GOPATH=/root/go CGO_ENABLED=0 /usr/local/go/bin/go install -ldflags='-s -w' github.com/simonw/rodney@latest",
    )
    .uv_pip_install("fastapi[standard]", "itsdangerous")
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
    # Each distinct agent_id gets its own container / Chromium (Modal parametrized cls).
    agent_id: str = modal.parameter()

    @modal.enter()
    def start_chrome(self):
        """Start Chromium via rodney on container boot."""
        result = subprocess.run(
            ["rodney", "status"], capture_output=True, text=True
        )
        if result.returncode != 0:
            subprocess.run(
                ["rodney", "start", "--global"],
                capture_output=True, text=True, check=True,
            )

    @modal.exit()
    def stop_chrome(self):
        """Kill Chrome on container shutdown."""
        subprocess.run(
            ["rodney", "--global", "stop"],
            capture_output=True, text=True,
        )

    @modal.method()
    def status(self) -> dict:
        result = subprocess.run(
            ["rodney", "--global", "status"],
            capture_output=True, text=True,
        )
        return {"exit_code": result.returncode, "output": result.stdout.strip()}

    @modal.method()
    def screenshot(self) -> dict:
        import os
        shot_path = "/tmp/shot.png"
        result = subprocess.run(
            ["rodney", "--global", "screenshot", shot_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}
        with open(shot_path, "rb") as f:
            png = f.read()
        os.remove(shot_path)
        return {"png": png}

    @modal.method()
    def get_url(self) -> dict:
        result = subprocess.run(
            ["rodney", "--global", "url"],
            capture_output=True, text=True,
        )
        return {"url": result.stdout.strip(), "exit_code": result.returncode}

    @modal.method()
    def get_title(self) -> dict:
        result = subprocess.run(
            ["rodney", "--global", "title"],
            capture_output=True, text=True,
        )
        return {"title": result.stdout.strip(), "exit_code": result.returncode}

    @modal.method()
    def open_url(self, url: str) -> dict:
        result = subprocess.run(
            ["rodney", "--global", "open", url],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # e.g. user ran `rodney stop` — same start as @modal.enter
            subprocess.run(
                ["rodney", "start", "--global"],
                capture_output=True, text=True,
            )
            result = subprocess.run(
                ["rodney", "--global", "open", url],
                capture_output=True, text=True,
            )
        return {"exit_code": result.returncode, "stderr": result.stderr.strip()}

    @modal.method()
    def run_js(self, expression: str) -> dict:
        result = subprocess.run(
            ["rodney", "--global", "js", expression],
            capture_output=True, text=True,
        )
        return {
            "result": result.stdout.strip(),
            "exit_code": result.returncode,
            "stderr": result.stderr.strip(),
        }

    @modal.method()
    def click(self, x: int, y: int) -> dict:
        js_code = f"""(function(){{
            var el=document.elementFromPoint({x},{y});
            if(!el) return 'no element';
            el.dispatchEvent(new MouseEvent('mouseover',{{bubbles:true,clientX:{x},clientY:{y}}}));
            el.dispatchEvent(new MouseEvent('mousedown',{{bubbles:true,clientX:{x},clientY:{y}}}));
            el.dispatchEvent(new MouseEvent('mouseup',{{bubbles:true,clientX:{x},clientY:{y}}}));
            el.dispatchEvent(new MouseEvent('click',{{bubbles:true,cancelable:true,clientX:{x},clientY:{y}}}));
            return el.tagName+(el.id?'#'+el.id:'');
        }})()"""
        result = subprocess.run(
            ["rodney", "--global", "js", js_code],
            capture_output=True, text=True,
        )
        return {"exit_code": result.returncode, "clicked": result.stdout.strip()}

    @modal.method()
    def scroll(self, dx: int, dy: int) -> dict:
        result = subprocess.run(
            ["rodney", "--global", "js", f"window.scrollBy({dx},{dy})"],
            capture_output=True, text=True,
        )
        return {"exit_code": result.returncode}

    @modal.method()
    def viewport(self) -> dict:
        result = subprocess.run(
            ["rodney", "--global", "js",
             "JSON.stringify({w:window.innerWidth,h:window.innerHeight})"],
            capture_output=True, text=True,
        )
        return {"exit_code": result.returncode, "viewport": result.stdout.strip()}

    @modal.method()
    def run_command(self, args: list[str], timeout: int = 30) -> dict:
        import base64
        MAX_TIMEOUT = 120
        result = subprocess.run(
            ["rodney", "--global"] + args,
            capture_output=True,
            timeout=min(timeout, MAX_TIMEOUT),
        )
        stdout = result.stdout
        is_binary = False
        try:
            stdout_str = stdout.decode("utf-8")
        except UnicodeDecodeError:
            stdout_str = base64.b64encode(stdout).decode("ascii")
            is_binary = True
        return {
            "exit_code": result.returncode,
            "stdout": stdout_str,
            "stderr": result.stderr.decode("utf-8", errors="replace"),
            "is_binary": is_binary,
        }


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

    VALID_TOKENS = set(os.environ["RODNEY_API_TOKENS"].split(","))
    cookie_signer = URLSafeTimedSerializer(os.environ["RODNEY_COOKIE_SECRET"])
    COOKIE_NAME = "rodney_session"
    COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days

    def _token_is_valid(candidate: str) -> bool:
        """Constant-time check against all valid tokens to prevent timing attacks."""
        candidate_b = candidate.encode("utf-8")
        return any(
            hmac.compare_digest(candidate_b, valid.encode("utf-8")) for valid in VALID_TOKENS
        )

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
                token = cookie_signer.loads(cookie, max_age=COOKIE_MAX_AGE)
                if _token_is_valid(token):
                    return token
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
        signed = cookie_signer.dumps(token)
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            signed,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=False,  # Modal terminates TLS at the proxy; ASGI sees HTTP
        )
        return response

    @web_app.get("/logout")
    def logout():
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie(COOKIE_NAME)
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
