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
uv sync              # install dependencies
modal setup          # one-time — authenticates with Modal
./modal-deployment/deploy.sh  # deploys and prints your URL + token
```

The script will print your **login URL** and **API token**. Open the login URL on your phone and enter the token.

### Choose your own password

Don't want to paste a random token on your phone? Set your own:

```bash
RODNEY_PASSWORD=mypassword ./deploy.sh
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

All endpoints require auth (`Authorization: Bearer <token>` header or session cookie).

| Method | Endpoint      | Description                        |
|--------|---------------|------------------------------------|
| POST   | `/open`       | Navigate (`?url=https://...`)      |
| GET    | `/screenshot` | Returns PNG                        |
| GET    | `/title`      | Current page title                 |
| GET    | `/url`        | Current page URL                   |
| POST   | `/js`         | Run JavaScript (`?expression=...`) |
| POST   | `/run`        | Universal command (JSON body)      |
| GET    | `/status`     | Chrome/rodney status               |
| GET    | `/docs`       | Interactive Swagger UI             |

### The `/run` endpoint

Send any rodney command as JSON:

```json
{"args": ["click", "#submit-button"], "timeout": 10}
{"args": ["type", "#search", "hello world"]}
{"args": ["text", "h1"]}
```

### iOS Shortcuts / Android Tasker

Point an HTTP action at your Modal URL with:
- Header: `Authorization: Bearer your-token`
- Body: JSON with `args` array

## Troubleshooting

**Slow first request** — If `min_containers=1` in `deploy.py`, one container stays warm. Set to 0 to save money (adds ~10-15s cold start).

**Check logs:**
```bash
modal app logs rodney
```

**Detailed setup docs:** See [modal-deployment/SETUP.md](modal-deployment/SETUP.md).

## License

[MIT](LICENSE)
