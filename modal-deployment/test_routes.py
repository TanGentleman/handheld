"""Every browser route, flat and per-agent, against a recording fake backend."""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ENV = {"RODNEY_API_TOKENS": "tok-1", "RODNEY_COOKIE_SECRET": "secret"}
AUTH = {"Authorization": "Bearer tok-1"}


class FakeAgent:
    def __init__(self, log, agent_id):
        self.log, self.agent_id = log, agent_id

    def __getattr__(self, name):
        def method(*args):
            self.log.append((self.agent_id, name, args))
            if name == "screenshot":
                return {"png": b"\x89PNG"}
            return {"method": name, "args": list(args)}

        return method


class FakeBackend:
    def __init__(self):
        self.registry, self.log, self.forgotten = {}, [], []

    def agent(self, agent_id):
        return FakeAgent(self.log, agent_id)

    def forget(self, agent_id):
        self.forgotten.append(agent_id)


@pytest.fixture()
def setup():
    backend = FakeBackend()
    with patch.dict(os.environ, ENV):
        from deploy import create_app

        client = TestClient(create_app(backend=backend), base_url="https://testserver")
    backend.registry["a1"] = {"purpose": "x", "status": "active", "created_at": ""}
    return client, backend


# (method, path after the prefix, json body, backend method, args)
CASES = [
    ("GET", "/status", None, "status", ()),
    ("GET", "/url", None, "get_url", ()),
    ("GET", "/title", None, "get_title", ()),
    ("GET", "/viewport", None, "viewport", ()),
    ("POST", "/open?url=https://example.com", None, "open_url", ("https://example.com",)),
    ("POST", "/js?expression=1%2B1", None, "run_js", ("1+1",)),
    ("POST", "/click?x=3&y=4", None, "click", (3, 4)),
    ("POST", "/scroll?dy=200", None, "scroll", (0, 200)),
    ("POST", "/run", {"args": ["text", "h1"]}, "run_command", (["text", "h1"], 30)),
    ("POST", "/run", {"args": ["text", "h1"], "timeout": 5}, "run_command", (["text", "h1"], 5)),
]


@pytest.mark.parametrize("prefix,agent_id", [("", "default"), ("/agents/a1", "a1")])
@pytest.mark.parametrize("method,path,body,name,args", CASES)
def test_route_calls_backend(setup, prefix, agent_id, method, path, body, name, args):
    client, backend = setup
    r = client.request(method, prefix + path, json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"method": name, "args": list(args)}
    assert backend.log == [(agent_id, name, args)]


@pytest.mark.parametrize("prefix", ["", "/agents/a1"])
def test_screenshot_png(setup, prefix):
    client, _ = setup
    r = client.get(prefix + "/screenshot", headers=AUTH)
    assert (r.status_code, r.headers["content-type"], r.content) == (200, "image/png", b"\x89PNG")


@pytest.mark.parametrize("prefix", ["", "/agents/a1"])
def test_screenshot_error_is_500_json(setup, prefix):
    client, backend = setup
    backend.agent = lambda agent_id: type("A", (), {"screenshot": lambda self: {"error": "boom"}})()
    r = client.get(prefix + "/screenshot", headers=AUTH)
    assert r.status_code == 500
    assert r.json() == {"error": "boom"}


@pytest.mark.parametrize("prefix", ["", "/agents/a1"])
def test_run_rejects_empty_args(setup, prefix):
    client, backend = setup
    r = client.post(prefix + "/run", json={"args": []}, headers=AUTH)
    assert r.status_code == 422
    assert all(entry[1] != "run_command" for entry in backend.log)


def test_unknown_agent_is_404(setup):
    client, backend = setup
    assert client.get("/agents/nope/status", headers=AUTH).status_code == 404
    assert backend.log == []


def test_flat_routes_create_default_agent(setup):
    client, backend = setup
    assert "default" not in backend.registry
    client.get("/title", headers=AUTH)
    assert backend.registry["default"]["purpose"] == "default"


@pytest.mark.parametrize("prefix", ["", "/agents/a1"])
@pytest.mark.parametrize("method,path,body,name,args", CASES)
def test_route_requires_auth_before_touching_backend(setup, prefix, method, path, body, name, args):
    client, backend = setup
    r = client.request(method, prefix + path, json=body)
    assert r.status_code == 401
    assert backend.log == []
    assert "default" not in backend.registry


def test_delete_forgets_agent(setup):
    client, backend = setup
    assert client.delete("/agents/a1", headers=AUTH).json() == {"deleted": "a1"}
    assert backend.forgotten == ["a1"]
    assert client.get("/agents/a1/status", headers=AUTH).status_code == 404


def test_route_table():
    with patch.dict(os.environ, ENV):
        from deploy import create_app

        routes = sorted(
            (m, r.path) for r in create_app(backend=FakeBackend()).routes for m in getattr(r, "methods", ())
        )
    browser = [
        ("GET", "/screenshot"), ("GET", "/status"), ("GET", "/title"), ("GET", "/url"),
        ("GET", "/viewport"), ("POST", "/click"), ("POST", "/js"), ("POST", "/open"),
        ("POST", "/run"), ("POST", "/scroll"),
    ]
    expected = sorted(
        browser
        + [(m, "/agents/{agent_id}" + p) for m, p in browser]
        + [
            ("GET", "/"), ("GET", "/login"), ("POST", "/login"), ("GET", "/logout"),
            ("GET", "/agents"), ("POST", "/agents"), ("GET", "/agents/summary"),
            ("GET", "/agents/{agent_id}"), ("DELETE", "/agents/{agent_id}"),
            ("GET", "/agents/{agent_id}/ui"),
            ("GET", "/docs"), ("HEAD", "/docs"), ("GET", "/openapi.json"), ("HEAD", "/openapi.json"),
            ("GET", "/docs/oauth2-redirect"), ("HEAD", "/docs/oauth2-redirect"),
            ("GET", "/redoc"), ("HEAD", "/redoc"),
        ]
    )
    assert routes == expected
