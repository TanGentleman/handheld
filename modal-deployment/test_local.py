"""Local mode: the real app over plain HTTP, with LocalBackend driving a fake rodney."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import local
from local import LocalBackend
from test_browser import fake  # noqa: F401  (pytest fixture)

TOKEN = "local-token-abc"
ENV = {"RODNEY_API_TOKENS": TOKEN, "RODNEY_COOKIE_SECRET": "local-cookie-secret"}


@pytest.fixture()
def app(fake, tmp_path):  # noqa: F811
    rodney, calls = fake
    backend = LocalBackend(str(tmp_path / "homes"), binary=rodney.binary)
    with patch.dict(os.environ, ENV):
        from deploy import create_app

        client = TestClient(create_app(backend=backend, secure_cookie=False), base_url="http://127.0.0.1")
    yield client, backend, calls, tmp_path / "homes"
    backend.close()


def test_login_cookie_works_over_http(app):
    client, _, _, _ = app
    r = client.post("/login", data={"token": TOKEN}, follow_redirects=False)
    assert r.status_code == 303
    assert "secure" not in r.headers["set-cookie"].lower()
    assert "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/agents").status_code == 200


def test_still_requires_auth(app):
    client, _, calls, _ = app
    assert client.get("/status").status_code == 401
    assert client.get("/status", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert calls() == []


def test_each_agent_gets_its_own_rodney_home(app):
    client, _, calls, homes = app
    h = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/title", headers=h).json() == {"title": "Fake Title", "exit_code": 0}
    aid = client.post("/agents", headers=h, json={}).json()["agent_id"]
    assert client.get(f"/agents/{aid}/url", headers=h).json()["url"] == "https://example.com/"
    by_home = {}
    for c in calls():
        by_home.setdefault(c["home"], []).append(c["args"][1])
    assert set(by_home) == {str(homes / "default"), str(homes / aid)}
    # started once each, on first use
    assert by_home[str(homes / "default")][:2] == ["status", "start"]
    assert by_home[str(homes / "default")].count("start") == 1


def test_screenshot_png(app):
    client, _, _, _ = app
    r = client.get("/screenshot", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_delete_stops_that_browser(app):
    client, backend, calls, homes = app
    h = {"Authorization": f"Bearer {TOKEN}"}
    aid = client.post("/agents", headers=h, json={}).json()["agent_id"]
    assert client.delete(f"/agents/{aid}", headers=h).status_code == 200
    assert calls()[-1] == {"args": ["--global", "stop"], "home": str(homes / aid)}
    assert aid not in backend._browsers


def test_close_stops_every_browser(app):
    client, backend, calls, _ = app
    h = {"Authorization": f"Bearer {TOKEN}"}
    client.get("/status", headers=h)
    client.post("/agents", headers=h, json={})
    backend.close()
    assert [c["args"][1] for c in calls()].count("stop") == 2


def test_main_exits_without_rodney(monkeypatch):
    monkeypatch.setattr(local.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="rodney not found"):
        local.main([])


def test_main_generates_token_and_cleans_up(monkeypatch, capsys):
    monkeypatch.setattr(local.shutil, "which", lambda name: "/usr/bin/rodney")
    seen = {}

    def fake_run(app, host, port):
        seen.update(host=host, port=port, token=os.environ["RODNEY_API_TOKENS"])

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    with patch.dict(os.environ, clear=False):  # main() writes the generated token here
        os.environ.pop("RODNEY_API_TOKENS", None)
        os.environ.pop("RODNEY_COOKIE_SECRET", None)
        local.main(["--port", "9999"])
    assert "RODNEY_API_TOKENS" not in os.environ or os.environ["RODNEY_API_TOKENS"] != seen["token"]
    out = capsys.readouterr().out
    assert seen["host"] == "127.0.0.1" and seen["port"] == 9999
    assert f"login token: {seen['token']}" in out
    assert len(seen["token"]) >= 24
    state_dir = out.split("browser state in ")[1].split(")")[0]
    assert not Path(state_dir).exists()
