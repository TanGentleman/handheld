# Deploying Handheld on Modal

Run a headless Chrome instance in the cloud and control it from any device. Handheld uses the [Rodney](https://github.com/simonw/rodney) CLI inside the container.

## What you'll end up with

A Modal container running:

- **Chromium** (headless)
- **Rodney** (CLI for Chrome automation)
- **HTTP API** (FastAPI wrapping rodney commands)

You'll be able to do things like:

```
curl https://your-username--rodney-api.modal.run/open?url=https://news.ycombinator.com
curl https://your-username--rodney-api.modal.run/screenshot -o page.png
curl https://your-username--rodney-api.modal.run/title
```

Modal gives you a public HTTPS URL with token auth, and a Swagger UI at
`/docs` for quick use from a phone browser.

---

## Prerequisites

- A [Modal](https://modal.com) account
- Python 3.10+ on your local machine
- This repo cloned locally

---

## Step 1: Install the Modal CLI

```bash
pip install modal
```

Then authenticate — this opens a browser window:

```bash
modal setup
```

You should see `Modal token stored successfully` when done.

## Step 2: Create auth tokens

```bash
modal secret create rodney-auth \
  RODNEY_API_TOKENS=your-token-here \
  RODNEY_COOKIE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

Or just run `./deploy.sh` — it auto-generates tokens if the secret doesn't exist yet.

## Step 3: Deploy

From the `modal-deployment/` directory:

```bash
cd modal-deployment

# Test it first (hot-reload, temporary URL):
modal serve deploy.py

# When satisfied, deploy for real (persistent URL):
modal deploy deploy.py

# Or use the deploy script (handles secrets + deploy):
./deploy.sh
```

The first deploy takes a few minutes to build the image (installs Chromium,
compiles rodney from source, etc). Subsequent deploys reuse cached layers
and are much faster.

Modal will print a URL like:

```
https://your-username--rodney-api.modal.run
```

## Step 4: Verify it works

Open the Swagger docs in any browser:

```
https://your-username--rodney-api.modal.run/docs
```

Or use curl with a bearer token:

```bash
curl -H "Authorization: Bearer your-token" https://your-username--rodney-api.modal.run/status
curl -X POST -H "Authorization: Bearer your-token" "https://your-username--rodney-api.modal.run/open?url=https://example.com"
curl -H "Authorization: Bearer your-token" https://your-username--rodney-api.modal.run/title
curl -H "Authorization: Bearer your-token" https://your-username--rodney-api.modal.run/screenshot -o page.png
```

Or log in via the browser at `/login` with your token to get a session cookie.

---

## API Reference

| Method | Endpoint      | Description                        |
| ------ | ------------- | ---------------------------------- |
| GET    | `/status`     | Chrome/rodney status               |
| GET    | `/screenshot` | Returns a PNG image                |
| GET    | `/url`        | Current page URL                   |
| GET    | `/title`      | Current page title                 |
| POST   | `/open`       | Navigate (query param: `url=...`)  |
| POST   | `/js`         | Run JS (query param: `expression`) |
| POST   | `/run`        | Run any rodney command (see below) |
| GET    | `/docs`       | Interactive Swagger UI             |

### `/run` — the universal endpoint

Accepts any rodney command as a JSON body:

```json
{
  "args": ["click", "#submit-button"],
  "timeout": 10
}
```

Response:

```json
{
  "exit_code": 0,
  "stdout": "",
  "stderr": "",
  "is_binary": false
}
```

---

## Using from your phone

Open `/login` in your phone browser, enter your token, and you'll get a
session cookie. Then use the browser UI at `/` or Swagger at `/docs`.

For iOS Shortcuts or Android Tasker, use bearer token auth:

```
Authorization: Bearer your-token
```

Point HTTP actions at `/run` with a JSON body.

---

## Troubleshooting

**Screenshot returns an error**

- The first request auto-starts Chrome. If it fails, check Modal logs:
  `modal app logs rodney`

**Container keeps spinning down**

- The `min_containers=1` setting keeps one container alive. If cost is a
  concern, set it to 0 — the container will cold-start on each request
  (adds ~10-15s latency)

**Checking logs**

```bash
modal app logs rodney
```
