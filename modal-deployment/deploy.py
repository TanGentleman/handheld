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
import modal

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
    .env(
        {
            "ROD_CHROME_BIN": "/usr/bin/chromium",
            "RODNEY_HOME": "/root/.rodney",
            "PATH": "/root/go/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
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


# --- Login page ---

LOGIN_PAGE = string.Template("""\
<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Handheld</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:#0a0a0a;color:#e0e0e0;font-family:'SF Mono',ui-monospace,'Fira Code',monospace;
}
.card{
  background:#141414;border:1px solid #2a2a2a;border-radius:16px;
  padding:2.5rem;width:min(380px,90vw);box-shadow:0 16px 48px rgba(0,0,0,.6);
}
h1{font-size:1.5rem;font-weight:500;letter-spacing:-.02em;color:#fff}
p.sub{color:#666;font-size:.8rem;margin-top:.4rem;margin-bottom:2rem}
input[type=password]{
  width:100%;padding:.8rem 1rem;background:#0a0a0a;border:1px solid #333;
  border-radius:10px;color:#fff;font-family:inherit;font-size:.95rem;
  margin-bottom:1rem;outline:none;transition:border-color .2s;
}
input[type=password]:focus{border-color:#646cff}
input[type=password]::placeholder{color:#444}
button{
  width:100%;padding:.8rem;background:#646cff;color:#fff;border:none;
  border-radius:10px;font-size:.95rem;cursor:pointer;font-family:inherit;
  font-weight:500;transition:background .15s,transform .1s;
}
button:hover{background:#535bf2}
button:active{transform:scale(.98)}
.error{color:#ff6b6b;font-size:.8rem;margin-bottom:1rem}
</style>
</head><body>
<div class="card">
  <h1>Handheld</h1>
  <p class="sub">enter your access token</p>
  $error
  <form method="POST" action="/login">
    <input type="password" name="token" placeholder="token" autofocus required>
    <button type="submit">authenticate</button>
  </form>
</div>
</body></html>""")

# --- Browser UI (scoped to an agent) ---

UI_PAGE = string.Template("""\
<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Handheld</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden}
body{
  display:flex;flex-direction:column;
  background:#0a0a0a;color:#e0e0e0;
  font-family:'SF Mono',ui-monospace,'Fira Code',monospace;
}

/* URL bar */
#urlbar{
  display:flex;align-items:center;gap:6px;
  padding:8px 10px;background:#141414;border-bottom:1px solid #2a2a2a;
  flex-shrink:0;
}
#urlbar button{
  width:36px;height:36px;border:none;border-radius:8px;
  background:#1e1e1e;color:#999;font-size:1rem;cursor:pointer;
  display:flex;align-items:center;justify-content:center;flex-shrink:0;
}
#urlbar button:hover{background:#2a2a2a;color:#fff}
#urlbar button:active{transform:scale(.93)}
#nav-form{flex:1;display:flex}
#url-input{
  flex:1;padding:8px 12px;background:#0a0a0a;border:1px solid #333;
  border-radius:8px;color:#fff;font-family:inherit;font-size:.85rem;
  outline:none;min-width:0;
}
#url-input:focus{border-color:#646cff}

/* Status bar */
#statusbar{
  padding:4px 12px;background:#111;border-bottom:1px solid #1a1a1a;
  font-size:.7rem;color:#555;display:flex;justify-content:space-between;
  flex-shrink:0;
}
#statusbar .title{color:#888;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
#statusbar .indicator{width:8px;height:8px;border-radius:50%;background:#333;flex-shrink:0;margin-left:8px;align-self:center}
#statusbar .indicator.live{background:#4ade80}
#statusbar .indicator.busy{background:#facc15}

/* Screenshot viewport */
#viewport{
  flex:1;overflow:auto;position:relative;display:flex;
  align-items:flex-start;justify-content:center;background:#000;
}
#screen{
  width:100%;height:auto;display:block;
  image-rendering:auto;
}
#click-overlay{
  position:absolute;top:0;left:0;width:100%;height:100%;
  cursor:crosshair;
}
.ripple{
  position:absolute;width:30px;height:30px;border-radius:50%;
  border:2px solid #646cff;pointer-events:none;
  animation:ripple-out .5s ease-out forwards;
}
@keyframes ripple-out{
  0%{transform:translate(-50%,-50%) scale(0);opacity:1}
  100%{transform:translate(-50%,-50%) scale(2);opacity:0}
}

/* Bottom toolbar */
#toolbar{
  display:flex;align-items:center;justify-content:space-around;
  padding:8px 4px;background:#141414;border-top:1px solid #2a2a2a;
  flex-shrink:0;
}
#toolbar button{
  width:44px;height:44px;border:none;border-radius:10px;
  background:transparent;color:#999;font-size:1.2rem;cursor:pointer;
  display:flex;align-items:center;justify-content:center;
}
#toolbar button:hover{background:#1e1e1e;color:#fff}
#toolbar button:active{transform:scale(.9)}
#toolbar button.active{color:#646cff}

/* Modals / drawers */
.overlay{
  display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;
}
.overlay.show{display:flex;align-items:flex-end;justify-content:center}
.drawer{
  background:#141414;border:1px solid #2a2a2a;border-radius:16px 16px 0 0;
  padding:20px;width:100%;max-width:500px;max-height:60vh;overflow-y:auto;
}
.drawer h3{font-size:.9rem;font-weight:500;color:#fff;margin-bottom:12px}
.drawer input[type=text]{
  width:100%;padding:8px 12px;background:#0a0a0a;border:1px solid #333;
  border-radius:8px;color:#fff;font-family:inherit;font-size:.85rem;
  outline:none;margin-bottom:8px;
}
.drawer input[type=text]:focus{border-color:#646cff}
.drawer button.primary{
  width:100%;padding:10px;background:#646cff;color:#fff;border:none;
  border-radius:8px;font-size:.85rem;cursor:pointer;font-family:inherit;
  font-weight:500;margin-top:4px;
}
.drawer button.primary:hover{background:#535bf2}
.drawer pre{
  margin-top:10px;padding:10px;background:#0a0a0a;border:1px solid #2a2a2a;
  border-radius:8px;font-size:.75rem;color:#888;overflow-x:auto;
  white-space:pre-wrap;word-break:break-all;max-height:150px;overflow-y:auto;
  display:none;
}
.drawer pre.has-output{display:block}
.cmd-row{
  display:flex;gap:8px;margin-bottom:10px;
}
.cmd-row button{
  flex:1;padding:9px 8px;background:#1e1e1e;color:#ccc;border:1px solid #333;
  border-radius:8px;font-size:.8rem;cursor:pointer;font-family:inherit;
}
.cmd-row button:hover{background:#2a2a2a;color:#fff;border-color:#444}
.cmd-row button:active{transform:scale(.97)}
</style>
</head><body>

<!-- URL bar -->
<div id="urlbar">
  <button id="btn-back" title="Back">&#9664;</button>
  <button id="btn-fwd" title="Forward">&#9654;</button>
  <form id="nav-form">
    <input type="text" id="url-input" placeholder="Enter URL..." autocapitalize="none" autocorrect="off" spellcheck="false">
  </form>
  <button id="btn-go" title="Go">&#10148;</button>
</div>

<!-- Status bar -->
<div id="statusbar">
  <span class="title" id="page-title">connecting...</span>
  <span class="indicator" id="status-dot"></span>
</div>

<!-- Screenshot viewport -->
<div id="viewport">
  <img id="screen" alt="">
  <div id="click-overlay"></div>
</div>

<!-- Bottom toolbar -->
<div id="toolbar">
  <button id="btn-refresh" title="Refresh screenshot">&#8635;</button>
  <button id="btn-scroll-up" title="Scroll up">&#9650;</button>
  <button id="btn-scroll-down" title="Scroll down">&#9660;</button>
  <button id="btn-type" title="Type text">&#9000;</button>
  <button id="btn-cmd" title="Run command">&#9776;</button>
  <button id="btn-agents" title="Agents">&#9881;</button>
</div>

<!-- Type drawer -->
<div id="type-overlay" class="overlay">
  <div class="drawer">
    <h3>type text</h3>
    <input type="text" id="type-selector" placeholder="CSS selector (e.g. input[name=q])">
    <input type="text" id="type-text" placeholder="text to type">
    <button class="primary" id="btn-type-go">send</button>
  </div>
</div>

<!-- Command drawer -->
<div id="cmd-overlay" class="overlay">
  <div class="drawer">
    <h3>run command</h3>
    <div class="cmd-row">
      <button type="button" id="btn-cmd-start">start</button>
      <button type="button" id="btn-cmd-stop">stop</button>
      <button type="button" id="btn-cmd-status">status</button>
    </div>
    <input type="text" id="cmd-input" placeholder="e.g. click #submit">
    <button class="primary" id="btn-cmd-go">run</button>
    <pre id="cmd-output"></pre>
  </div>
</div>

<script>
(function(){
  // Agent ID is embedded by the server
  const AGENT_ID = '$agent_id';
  const BASE = '/agents/' + AGENT_ID;

  // --- State ---
  const S = { polling: true, busy: false, vpW: 1920, vpH: 1080, urlFocused: false };

  // --- Elements ---
  const $$ = id => document.getElementById(id);
  const screen    = $$('screen');
  const overlay   = $$('click-overlay');
  const urlInput  = $$('url-input');
  const titleEl   = $$('page-title');
  const dot       = $$('status-dot');

  // --- Helpers ---
  async function api(method, path, body) {
    S.busy = true; dot.className = 'indicator busy';
    try {
      const opts = { method };
      if (body) { opts.headers = {'Content-Type':'application/json'}; opts.body = JSON.stringify(body); }
      const r = await fetch(BASE + path, opts);
      if (r.status === 401) { window.location = '/login'; return null; }
      return r;
    } finally { S.busy = false; dot.className = 'indicator live'; }
  }

  // --- Screenshot polling ---
  async function refreshScreen() {
    try {
      const r = await fetch(BASE + '/screenshot');
      if (!r.ok) return;
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const old = screen.src;
      screen.src = url;
      if (old && old.startsWith('blob:')) URL.revokeObjectURL(old);
    } catch(e) { dot.className = 'indicator'; }
  }

  async function syncUrl() {
    if (S.urlFocused) return;
    try {
      const r = await fetch(BASE + '/url');
      if (!r.ok) return;
      const d = await r.json();
      if (d.url) urlInput.value = d.url;
    } catch(e) {}
  }

  async function syncTitle() {
    try {
      const r = await fetch(BASE + '/title');
      if (!r.ok) return;
      const d = await r.json();
      titleEl.textContent = d.title || 'untitled';
    } catch(e) {}
  }

  async function poll() {
    while (true) {
      if (S.polling && !S.busy) {
        await refreshScreen();
        // sync url/title every 3rd frame
        if (!S._c) S._c = 0;
        if (++S._c % 3 === 0) { syncUrl(); syncTitle(); }
      }
      await new Promise(r => setTimeout(r, 1000));
    }
  }

  // --- Viewport detection ---
  async function detectViewport() {
    try {
      const r = await api('GET', '/viewport');
      if (!r) return;
      const d = await r.json();
      const vp = JSON.parse(d.viewport);
      S.vpW = vp.w; S.vpH = vp.h;
    } catch(e) {}
  }

  // --- Click ---
  function showRipple(x, y) {
    const el = document.createElement('div');
    el.className = 'ripple';
    el.style.left = x + 'px'; el.style.top = y + 'px';
    $$('viewport').appendChild(el);
    setTimeout(() => el.remove(), 500);
  }

  overlay.addEventListener('click', async function(e) {
    const rect = screen.getBoundingClientRect();
    const scaleX = S.vpW / rect.width;
    const scaleY = S.vpH / rect.height;
    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);
    showRipple(e.clientX - $$('viewport').getBoundingClientRect().left,
               e.clientY - $$('viewport').getBoundingClientRect().top);
    await api('POST', '/click?x=' + x + '&y=' + y);
    await refreshScreen();
    syncUrl(); syncTitle();
  });

  // --- Navigation ---
  $$('nav-form').addEventListener('submit', async function(e) {
    e.preventDefault();
    let url = urlInput.value.trim();
    if (!url) return;
    if (!/^https?:\\/\\//.test(url)) url = 'https://' + url;
    urlInput.blur();
    await api('POST', '/open?url=' + encodeURIComponent(url));
    await refreshScreen();
    syncUrl(); syncTitle();
  });
  $$('btn-go').addEventListener('click', () => $$('nav-form').dispatchEvent(new Event('submit')));
  $$('btn-back').addEventListener('click', async () => {
    await api('POST', '/js?expression=' + encodeURIComponent('history.back()'));
    setTimeout(async () => { await refreshScreen(); syncUrl(); syncTitle(); }, 500);
  });
  $$('btn-fwd').addEventListener('click', async () => {
    await api('POST', '/js?expression=' + encodeURIComponent('history.forward()'));
    setTimeout(async () => { await refreshScreen(); syncUrl(); syncTitle(); }, 500);
  });

  urlInput.addEventListener('focus', () => { S.urlFocused = true; urlInput.select(); });
  urlInput.addEventListener('blur', () => { S.urlFocused = false; });

  // --- Toolbar ---
  $$('btn-refresh').addEventListener('click', async () => { await refreshScreen(); syncUrl(); syncTitle(); });
  $$('btn-scroll-up').addEventListener('click', async () => { await api('POST','/scroll?dy=-500'); await refreshScreen(); });
  $$('btn-scroll-down').addEventListener('click', async () => { await api('POST','/scroll?dy=500'); await refreshScreen(); });

  // --- Type drawer ---
  $$('btn-type').addEventListener('click', () => $$('type-overlay').classList.toggle('show'));
  $$('type-overlay').addEventListener('click', function(e) { if (e.target === this) this.classList.remove('show'); });
  $$('btn-type-go').addEventListener('click', async () => {
    const sel = $$('type-selector').value.trim();
    const txt = $$('type-text').value;
    if (!sel || !txt) return;
    await api('POST', '/run', { args: ['type', sel, txt] });
    $$('type-overlay').classList.remove('show');
    $$('type-selector').value = ''; $$('type-text').value = '';
    await refreshScreen();
  });

  // --- Command drawer ---
  $$('btn-cmd').addEventListener('click', () => $$('cmd-overlay').classList.toggle('show'));
  $$('cmd-overlay').addEventListener('click', function(e) { if (e.target === this) this.classList.remove('show'); });
  async function execRun(args) {
    const r = await api('POST', '/run', { args });
    if (!r) return;
    const d = await r.json();
    const out = $$('cmd-output');
    out.textContent = JSON.stringify(d, null, 2);
    out.classList.add('has-output');
    await refreshScreen();
  }
  $$('btn-cmd-start').addEventListener('click', () => execRun(['start']));
  $$('btn-cmd-stop').addEventListener('click', () => execRun(['stop']));
  $$('btn-cmd-status').addEventListener('click', () => execRun(['status']));
  $$('btn-cmd-go').addEventListener('click', async () => {
    const raw = $$('cmd-input').value.trim();
    if (!raw) return;
    const args = raw.match(/(?:[^\\s"]+|"[^"]*")+/g).map(s => s.replace(/^"|"$$/g,''));
    await execRun(args);
  });
  $$('cmd-input').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.preventDefault(); $$('btn-cmd-go').click(); }
  });

  // --- Agents button → go to dashboard ---
  $$('btn-agents').addEventListener('click', () => { window.location = '/'; });

  // --- Init ---
  detectViewport();
  dot.className = 'indicator live';
  poll();
})();
</script>
</body></html>
""")

# --- Agent dashboard UI ---

DASHBOARD_PAGE = """\
<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Handheld</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  min-height:100vh;
  background:#0a0a0a;color:#e0e0e0;
  font-family:'SF Mono',ui-monospace,'Fira Code',monospace;
}
.container{max-width:500px;margin:0 auto;padding:20px 16px}
h1{font-size:1.3rem;font-weight:500;letter-spacing:-.02em;color:#fff;margin-bottom:4px}
p.sub{color:#666;font-size:.75rem;margin-bottom:20px}

.agent-card{
  background:#141414;border:1px solid #2a2a2a;border-radius:12px;
  padding:16px;margin-bottom:10px;cursor:pointer;
  transition:border-color .15s,transform .1s;display:flex;
  justify-content:space-between;align-items:center;
}
.agent-card:hover{border-color:#646cff}
.agent-card:active{transform:scale(.98)}
.agent-info .agent-id{font-size:.85rem;color:#fff;font-weight:500}
.agent-info .agent-meta{font-size:.7rem;color:#666;margin-top:2px}
.agent-actions{display:flex;gap:6px}
.agent-actions button{
  width:32px;height:32px;border:none;border-radius:8px;
  background:#1e1e1e;color:#999;font-size:.8rem;cursor:pointer;
  display:flex;align-items:center;justify-content:center;
}
.agent-actions button:hover{background:#2a2a2a;color:#fff}
.agent-actions button.delete:hover{background:#3a1515;color:#ff6b6b}

#btn-new{
  width:100%;padding:12px;background:#646cff;color:#fff;border:none;
  border-radius:10px;font-size:.85rem;cursor:pointer;font-family:inherit;
  font-weight:500;margin-top:6px;transition:background .15s,transform .1s;
}
#btn-new:hover{background:#535bf2}
#btn-new:active{transform:scale(.98)}
#btn-new:disabled{opacity:.5;cursor:not-allowed}

.empty{text-align:center;color:#555;font-size:.8rem;padding:30px 0}
#agent-list{min-height:60px}
</style>
</head><body>
<div class="container">
  <h1>Handheld</h1>
  <p class="sub">browser agents</p>
  <div id="agent-list"><div class="empty">loading...</div></div>
  <button id="btn-new">+ new agent</button>
</div>

<script>
(function(){
  const list = document.getElementById('agent-list');
  const btnNew = document.getElementById('btn-new');

  async function loadAgents() {
    try {
      const r = await fetch('/agents');
      if (r.status === 401) { window.location = '/login'; return; }
      const agents = await r.json();
      if (agents.length === 0) {
        list.innerHTML = '<div class="empty">no agents running</div>';
        return;
      }
      list.innerHTML = agents.map(a => `
        <div class="agent-card" data-id="${a.id}">
          <div class="agent-info">
            <div class="agent-id">${a.id}</div>
            <div class="agent-meta">${a.purpose || 'browser agent'}</div>
          </div>
          <div class="agent-actions">
            <button class="delete" data-id="${a.id}" title="Delete">&#10005;</button>
          </div>
        </div>
      `).join('');

      // Click card → open agent UI
      list.querySelectorAll('.agent-card').forEach(card => {
        card.addEventListener('click', (e) => {
          if (e.target.closest('.delete')) return;
          window.location = '/agents/' + card.dataset.id + '/ui';
        });
      });

      // Delete buttons
      list.querySelectorAll('.delete').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const id = btn.dataset.id;
          await fetch('/agents/' + id, { method: 'DELETE' });
          loadAgents();
        });
      });
    } catch(e) {
      list.innerHTML = '<div class="empty">error loading agents</div>';
    }
  }

  btnNew.addEventListener('click', async () => {
    btnNew.disabled = true;
    try {
      const r = await fetch('/agents', { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
      if (r.ok) {
        const d = await r.json();
        window.location = '/agents/' + d.agent_id + '/ui';
      }
    } finally { btnNew.disabled = false; }
  });

  loadAgents();
})();
</script>
</body></html>
"""


# --- App factory (importable by tests) ---


def create_app():
    import json
    import os
    from datetime import datetime, timezone

    from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
    from fastapi.responses import HTMLResponse, RedirectResponse
    from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
    from pydantic import BaseModel

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
        return HTMLResponse(DASHBOARD_PAGE)

    @web_app.get("/agents/{agent_id}/ui", response_class=HTMLResponse)
    def agent_ui(agent_id: str, request: Request):
        try:
            require_auth(request)
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)
        _get_agent(agent_id)  # 404 if not found
        return HTMLResponse(UI_PAGE.substitute(agent_id=agent_id))

    # --- Auth routes (public) ---

    @web_app.get("/login", response_class=HTMLResponse)
    def login_page():
        return LOGIN_PAGE.substitute(error="")

    @web_app.post("/login")
    def login_submit(token: str = Form(...)):
        if not _token_is_valid(token):
            return HTMLResponse(
                LOGIN_PAGE.substitute(error='<p class="error">invalid token</p>'),
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
    min_containers=1,
    timeout=86400,
    scaledown_window=300,
)
@modal.asgi_app()
def api():
    return create_app()
