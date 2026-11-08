# handheld roadmap

handheld runs headless Chromium on Modal behind a small FastAPI app that shells out to
[rodney](https://github.com/simonw/rodney). You log in from your phone and drive the browser
through screenshots: tap to click, scroll, type, run any rodney command. For people who want
a real desktop browser (or several, one per "agent") they can poke at from a phone or an iOS
Shortcut.

## Status (upstream main 63094f0, checked 2026-11-08)

- `uv sync --extra test && cd modal-deployment && uv run pytest`: 35 passed, offline. The
  tests fake `BrowserAgent` and the `modal.Dict`; `import deploy` needs no Modal credentials.
- Not checked: a real Modal deploy (no account). The Modal API calls import cleanly on 1.6.3.
- Fail-open auth: an empty entry in `RODNEY_API_TOKENS` (`"tok,,"`, or `""`) made
  `Authorization: Bearer ` a valid credential (reproduced: 200 on `/status`).
- The session cookie carried the raw token (signed, not encrypted) and was not `Secure`.
- `rodney screenshot FILE` without `-h` is a full-page capture (1265x11134 on a Wikipedia
  article), so the phone UI showed the whole page, scroll did nothing visible and taps
  landed at the wrong y.
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
- `handheld-2` (planned): pull the rodney calls out of the Modal class into a plain `Rodney`
  driver; test its argv and error handling with a fake `rodney` on PATH.
- `handheld-3` (planned): `local` mode, the same app on your machine against local Chrome.
- `handheld-4` (planned): one router serves both `/agents/{id}/...` and the default-agent
  routes; fold SETUP.md into the README.

## Apply a branch upstream

```bash
# in kranti-sandbox
base=$(git log -1 --format=%H --grep='^Import handheld upstream' handheld-1)
git format-patch --relative=handheld "$base"..handheld-1 -o /tmp/handheld-1
# in a clean upstream clone at 63094f0
git clone https://github.com/TanGentleman/handheld && cd handheld
git am /tmp/handheld-1/*.patch
uv sync --locked && uv run pytest
```

For a stacked branch, use the previous branch as the base: `handheld-1..handheld-2`.

## Needs Tan

- Not deployed: the pinned image (Go 1.26.10, rodney v0.4.0, Debian `chromium`) and modal
  1.6.3 were checked locally only. Run `./modal-deployment/deploy.sh` once.
- Deploying logs everyone out once (cookie format changed; deploy.sh rotates the secret
  anyway).
- Screenshot size is fixed at 1280x720 (rodney's default width). A phone-sized viewport
  would get mobile sites; your call.
- No login rate limit. With a long token that is fine; `RODNEY_PASSWORD` now warns under
  12 characters but does not refuse.
- `/docs` and `/openapi.json` stay public (they list routes, no data).
- Delete the stale `security-hardening` and merged branches upstream if you agree.
