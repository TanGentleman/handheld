# Handheld

Drive a headless Chromium from your phone: a FastAPI app wraps the [rodney](https://github.com/simonw/rodney) CLI in an HTTP API and a phone UI. Runs locally or on [Modal](https://modal.com).

```bash
git clone https://github.com/TanGentleman/handheld && cd handheld
uv sync --locked
go install github.com/simonw/rodney@v0.4.0   # puts rodney in ~/go/bin; add it to PATH
uv run modal-deployment/local.py             # prints a login token, serves http://127.0.0.1:8000/
```

Needs [uv](https://docs.astral.sh/uv/), Python 3.10+ and [Go](https://go.dev/dl/) 1.24.7+; a Modal account only to deploy. Details: [IMPLEMENTATION.md](IMPLEMENTATION.md), [ROADMAP.md](ROADMAP.md).

[MIT](LICENSE)
