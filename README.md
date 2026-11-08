# Handheld

Control a cloud browser from your phone.

Handheld deploys a headless Chromium instance on [Modal](https://modal.com) and wraps it in an HTTP API (via the [Rodney](https://github.com/simonw/rodney) CLI). Open the URL on your phone, log in, and you get a browser-in-a-browser you can tap, scroll, and type into.

## Prerequisites

- A [Modal](https://modal.com) account (free tier works)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

## Quick Start

```bash
git clone https://github.com/TanGentleman/handheld.git
cd handheld
uv sync              # install dependencies (and the test tools)
modal setup          # one-time — authenticates with Modal
./modal-deployment/deploy.sh  # deploys and prints your URL + token
```

The script will print your **login URL** and **API token**. Open the login URL on your phone and enter the token.

### Or run it locally first

No Modal account: the same UI and API against Chrome on your machine. Needs
[Go](https://go.dev/dl/) for rodney.

```bash
go install github.com/simonw/rodney@v0.4.0   # puts rodney in ~/go/bin
uv run modal-deployment/local.py             # prints a login token and http://127.0.0.1:8000/
```

Each agent is its own Chrome. `--host 0.0.0.0` makes it reachable from your phone on the
same network; that is plain HTTP, so the token travels unencrypted. Without
`ROD_CHROME_BIN=/path/to/chrome`, rodney downloads its own Chromium on first start.

### Choose your own password

Don't want to paste a random token on your phone? Set your own:

```bash
RODNEY_PASSWORD=a-long-passphrase ./modal-deployment/deploy.sh
```

Your token is saved to `modal-deployment/.env` so you can always recover it.

## Manual Deploy (no script)

```bash
uv sync
modal setup

# Create auth secret
modal secret create rodney-auth \
  RODNEY_API_TOKENS=your-password \
  RODNEY_COOKIE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Deploy
uv run modal deploy modal-deployment/deploy.py
```

## Using Handheld

### Phone browser UI

After logging in, the root URL (`/`) gives you a full browser interface with:
- Screenshot viewport — tap to click
- URL bar with back/forward
- Scroll, type, and command controls

### API Endpoints

Everything except `/login`, `/docs` and `/openapi.json` requires auth
(`Authorization: Bearer <token>` header or the session cookie set by `/login`).

These act on the `default` agent. The same routes exist per agent under
`/agents/{agent_id}/...`.

| Method | Endpoint      | Description                          |
|--------|---------------|--------------------------------------|
| POST   | `/open`       | Navigate (`?url=https://...`)        |
| GET    | `/screenshot` | PNG of the 1280x720 viewport         |
| GET    | `/title`      | Current page title                   |
| GET    | `/url`        | Current page URL                     |
| POST   | `/js`         | Run JavaScript (`?expression=...`)   |
| POST   | `/click`      | Click at viewport pixel (`?x=&y=`)   |
| POST   | `/scroll`     | Scroll by (`?dx=&dy=`)               |
| GET    | `/viewport`   | Window size                          |
| POST   | `/run`        | Any rodney command (JSON body)       |
| GET    | `/status`     | Chrome/rodney status                 |
| GET    | `/docs`       | Interactive Swagger UI (public)      |

Agents: `POST /agents` creates one (its own container and Chromium),
`GET /agents` lists them, `DELETE /agents/{agent_id}` removes it from the list.

### The `/run` endpoint

Send any [rodney command](https://github.com/simonw/rodney#usage) as JSON:

```json
{"args": ["click", "#submit-button"], "timeout": 10}
{"args": ["input", "#search", "hello world"]}
{"args": ["text", "h1"]}
```

### iOS Shortcuts / Android Tasker

Point an HTTP action at your Modal URL with:
- Header: `Authorization: Bearer your-token`
- Body: JSON with `args` array

## Troubleshooting

**Slow first request**: containers shut down after 5 minutes idle (`scaledown_window=300` in `deploy.py`), so the next request cold-starts Chromium. Add `min_containers=1` to the `@app.function` and `@app.cls` decorators to keep one warm, at the cost of paying for it.

**Check logs:**
```bash
modal app logs rodney
```

**Tests** (offline, no Modal account needed): `uv run pytest`. With rodney and Chrome
installed, `HANDHELD_LIVE=1 uv run pytest -k live` also drives a real browser.

**Detailed setup docs:** See [modal-deployment/SETUP.md](modal-deployment/SETUP.md).

## License

[MIT](LICENSE)
