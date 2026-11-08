# Handheld

Drive a headless Chromium from your phone. A FastAPI app wraps the
[rodney](https://github.com/simonw/rodney) CLI in an HTTP API and a phone UI: you see
screenshots, tap to click, scroll, type, or run any rodney command. It runs on
[Modal](https://modal.com) or on your own machine.

## Quick start (local, no Modal account)

Needs [uv](https://docs.astral.sh/uv/) (CI uses uv 0.13.7), Python 3.10+, and
[Go](https://go.dev/dl/) 1.24.7+ to build rodney.

```bash
git clone https://github.com/TanGentleman/handheld && cd handheld
uv sync --locked
go install github.com/simonw/rodney@v0.4.0   # puts rodney in ~/go/bin; add it to PATH
uv run modal-deployment/local.py             # prints a login token, serves http://127.0.0.1:8000/
```

Open `http://127.0.0.1:8000/` and enter the token, or call the API:

```bash
TOKEN=...   # printed by local.py
curl -X POST -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8000/open?url=https://example.com"
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/title     # {"title":"Example Domain","exit_code":0}
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/screenshot -o page.png
```

`local.py [--host 127.0.0.1] [--port 8000]`. Each agent gets its own Chrome; browser state
lives in a temp directory removed on exit.

## Deploy to Modal

Needs a Modal account.

```bash
uv sync --locked
uv run modal setup                  # one-time login
./modal-deployment/deploy.sh        # creates the rodney-auth secret, deploys, prints URL and token
```

`deploy.sh` saves the token to `modal-deployment/.env` (mode 600). It recreates the secret on
every run, which logs out existing sessions. The first deploy builds the image (Debian
`chromium`, Go 1.27.3, rodney v0.4.0, Python 3.12) and takes a few minutes. Modal prints a URL
like `https://<workspace>--rodney-api.modal.run`; use it in place of `http://127.0.0.1:8000`
above.

Without the script:

```bash
uv run modal secret create rodney-auth \
  RODNEY_API_TOKENS=your-token \
  RODNEY_COOKIE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
uv run modal deploy modal-deployment/deploy.py   # or `modal serve` for a temporary URL
uv run modal app logs rodney
```

Containers stop after 5 minutes idle (`scaledown_window=300` in `deploy.py`), so the next
request cold-starts Chromium.

## Environment

| Variable | Used by | Effect |
| --- | --- | --- |
| `RODNEY_API_TOKENS` | app | Comma-separated accepted tokens. Empty entries are ignored; no token means every request is 401. `local.py` generates one if unset. |
| `RODNEY_COOKIE_SECRET` | app | Signs the session cookie. `local.py` generates one if unset. |
| `RODNEY_PASSWORD` | `deploy.sh` | Use this as the token instead of a random one (warns under 12 characters). |
| `ROD_CHROME_BIN` | rodney | Chrome to launch. Unset, rodney downloads its own Chromium on first start. |
| `HANDHELD_LIVE` | tests | `1` runs the live browser tests. |

## API

Everything except `/login`, `/logout`, `/docs`, `/redoc` and `/openapi.json` needs
`Authorization: Bearer <token>` or the cookie set by `/login`. These routes act on the
`default` agent; the same routes exist under `/agents/{agent_id}/...`.

| Method | Route | Effect |
| --- | --- | --- |
| POST | `/open?url=` | Navigate |
| GET | `/screenshot` | PNG of the 1280x720 viewport |
| GET | `/title`, `/url` | Current page title, URL |
| POST | `/js?expression=` | Run JavaScript |
| POST | `/click?x=&y=` | Click at a viewport pixel |
| POST | `/scroll?dx=&dy=` | Scroll by |
| GET | `/viewport` | Window size |
| GET | `/status` | Chrome/rodney status |
| GET | `/`, `/agents/{agent_id}/ui` | Agent dashboard, phone UI for one agent |
| POST | `/run` | Any rodney command |
| POST, GET | `/agents` | Create (own Chrome; on Modal its own container), list |
| GET | `/agents/summary` | Short per-agent list the dashboard polls |
| DELETE | `/agents/{agent_id}` | Remove from the list |

`/run` takes `{"args": ["input", "#search", "hello"], "timeout": 10}` and returns
`{"exit_code": 0, "stdout": "...", "stderr": "", "is_binary": false}`; output that is not UTF-8
comes back base64-encoded with `is_binary: true`. The same request works from an iOS Shortcut
or Tasker HTTP action.

## Test

```bash
uv sync --locked
uv run pytest                       # offline: fake rodney on disk, fake backend, no Modal credentials
bash -n modal-deployment/deploy.sh
HANDHELD_LIVE=1 ROD_CHROME_BIN=/path/to/chrome uv run pytest -k live   # needs rodney on PATH
```

CI runs the first three commands on Python 3.10 and 3.14.

## Boundaries

- The browser opens whatever URL you send it; anyone with a token controls it.
- Local mode serves plain HTTP with a non-Secure cookie. `--host 0.0.0.0` exposes it to your
  network with the token unencrypted.
- Verified: the offline tests, the live tests and the local quick start against Chrome on
  Linux. Not verified: a Modal deploy or `deploy.sh` past its login check (no Modal account),
  the phone UI on a real phone.

[MIT](LICENSE)
