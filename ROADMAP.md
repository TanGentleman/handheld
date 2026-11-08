# handheld roadmap

handheld runs headless Chromium on Modal behind a small FastAPI app that shells out to
[rodney](https://github.com/simonw/rodney). You log in from your phone and drive the browser
through screenshots: tap to click, scroll, type, run any rodney command. For people who want
a real desktop browser (or several, one per "agent") they can poke at from a phone or an iOS
Shortcut.

## Status (upstream main 63094f0, checked 2026-11-08)

- Tests are offline: they fake `BrowserAgent` and the `modal.Dict`; `import deploy` needs no
  Modal credentials. A real Modal deploy was not checked (no account).
- Fail-open auth: an empty entry in `RODNEY_API_TOKENS` (`"tok,,"`, or `""`) made
  `Authorization: Bearer ` a valid credential (reproduced: 200 on `/status`).
- The session cookie carried the raw token (signed, not encrypted) and was not `Secure`.
- `rodney screenshot FILE` without `-h` is a full-page capture (1265x11134 on a Wikipedia
  article), so the phone UI showed the whole page, scroll did nothing visible and taps
  landed at the wrong y.
- Chrome never started on container boot: `rodney status` exits 0 with "No active browser
  session", so the exit-code check skipped `rodney start`. Screenshots failed until an `open`
  restarted Chrome as a side effect (reproduced with rodney v0.4.0 locally).
- The type drawer and the README sent `rodney type`, which does not exist (`input` does).
- Image: `rodney@latest` built with Go 1.24.4, but rodney needs Go >= 1.24.7; Go 1.24 and
  1.25 are out of support. fastapi/itsdangerous unpinned in the image.
- `deploy.sh`: the "not logged into modal" check used `modal profile current`, which exits 0
  without credentials. README ran `./deploy.sh` from the wrong directory and documented a
  `min_containers=1` setting the code does not have. SETUP.md curl examples had no token.
- Links: modal.com, docs.astral.sh, github.com/simonw/rodney all resolve.
- rodney CLI (main a842432, v0.4.0): every flag deploy.py uses (`start`, `stop`, `status`,
  `open`, `url`, `title`, `js`, `screenshot -w -h`, `--global`) exists and was run against
  Chrome 156 locally.
- Upstream branch `security-hardening` (2026-03-24) predates the agents refactor; everything
  in it is on main or in `-1` except clamping click/scroll ints.

## Signal vs noise

Keep: the phone UI (`web/agent.html`), token + cookie auth, rodney as the browser driver,
`/run` as the escape hatch, `deploy.sh`.

Noise: ten flat routes that duplicate the `/agents/{id}/...` routes line for line; nine
near-identical `subprocess.run` wrappers inside the Modal class; SETUP.md repeats the README;
`modal-deployment/README.md` is a pointer. Nothing runs without a Modal account.

## Stack

- `handheld-1` (done): auth fails closed on empty tokens, digest-only Secure cookie,
  viewport screenshots and `input` in the type drawer, Go 1.26.10 + rodney v0.4.0 pinned,
  modal 1.6.3 / fastapi 0.162, `uv sync` installs test deps, CI, docs and deploy.sh fixes.
- `handheld-2` (done): rodney calls move to a plain `browser.Rodney` class; tests against a
  fake `rodney` on disk, plus an opt-in live test; Chrome now starts on container boot.
- `handheld-3` (done): `uv run modal-deployment/local.py`, the same app and UI against
  local Chrome, one rodney session per agent; `create_app` takes the backend; the agent UI
  shows its first frame on load instead of after 5s.
- `handheld-4` (done): the browser routes are registered once for both `/agents/{id}/...`
  and the default agent (pinned first by `test_routes.py`); SETUP.md and the
  `modal-deployment/README.md` pointer are folded into the README.
- `handheld-5`: fastapi 0.162.2 (lock and image), Go 1.27.3 in the image, dependency floors
  pinned to the locked releases (modal 1.6.3, httpx 0.28.1, itsdangerous 2.2.0, pytest 9.2.0).
  rodney v0.4.0 builds with Go 1.27.3 and the live tests pass with it against local Chrome.
  CI pins checkout v7.0.1 and setup-uv v10.3.3 by SHA, uv 0.13.7, ubuntu-24.04, Python 3.10
  and 3.14. `test_pins.py` keeps the image pins equal to `uv.lock`. README rewrite: local quick
  start first, env var table, boundaries.
- `handheld-6`: README cut to 79 words; details moved to IMPLEMENTATION.md.

## Needs Tan

- Not deployed: the pinned image (Go 1.27.3, rodney v0.4.0, Debian `chromium`, Python 3.12)
  and modal 1.6.3 were checked locally only. Run `./modal-deployment/deploy.sh` once. The
  image's Python stays 3.12 until a deploy can check a newer one.
- Deploying logs everyone out once (cookie format changed; deploy.sh rotates the secret
  anyway).
- Screenshot size is fixed at 1280x720 (rodney's default width). A phone-sized viewport
  would get mobile sites; your call.
- No login rate limit. With a long token that is fine; `RODNEY_PASSWORD` now warns under
  12 characters but does not refuse.
- Local mode serves plain HTTP with a non-Secure cookie, bound to 127.0.0.1 unless you pass
  `--host`. HTTPS (e.g. behind Tailscale) would be needed before using it over a network.
- `/` with an empty agent list has no agent to open until you create one or call a flat
  route (`/status` creates `default`). Unchanged from upstream.
- `/docs`, `/redoc` and `/openapi.json` stay public (they list routes, no data).
- Delete the stale `security-hardening` and merged branches upstream if you agree.
