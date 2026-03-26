"""
Auth tests for Handheld's FastAPI app.

Runs without Modal or Chromium — patches env vars, BrowserAgent,
and agent_registry so we can test auth flows in isolation.

    pytest test_auth.py -v
"""

import os
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

TOKENS = "alpha-token-aaa,bravo-token-bbb"
COOKIE_SECRET = "test-cookie-secret-for-signing"
ENV = {"RODNEY_API_TOKENS": TOKENS, "RODNEY_COOKIE_SECRET": COOKIE_SECRET}

# Fake return values that mimic BrowserAgent remote calls
FAKE_STATUS = {"exit_code": 0, "output": "ok"}
FAKE_SCREENSHOT = {"png": b"\x89PNG\r\n\x1a\nfakedata"}
FAKE_URL = {"url": "https://example.com", "exit_code": 0}
FAKE_TITLE = {"title": "Example", "exit_code": 0}
FAKE_RUN = {"exit_code": 0, "stdout": "ok", "stderr": "", "is_binary": False}


def _make_fake_agent():
    """Create a mock BrowserAgent whose .method.remote() calls return canned data."""
    agent = MagicMock()
    agent.status.remote.return_value = FAKE_STATUS
    agent.screenshot.remote.return_value = FAKE_SCREENSHOT
    agent.get_url.remote.return_value = FAKE_URL
    agent.get_title.remote.return_value = FAKE_TITLE
    agent.open_url.remote.return_value = {"exit_code": 0, "stderr": ""}
    agent.run_js.remote.return_value = {"result": "", "exit_code": 0, "stderr": ""}
    agent.click.remote.return_value = {"exit_code": 0, "clicked": "DIV"}
    agent.scroll.remote.return_value = {"exit_code": 0}
    agent.viewport.remote.return_value = {"exit_code": 0, "viewport": '{"w":1920,"h":1080}'}
    agent.run_command.remote.return_value = FAKE_RUN
    return agent


class FakeRegistry(dict):
    """dict subclass that also supports .keys() iteration like modal.Dict."""
    pass


@pytest.fixture()
def client():
    fake_registry = FakeRegistry()
    fake_agent = _make_fake_agent()

    with patch.dict(os.environ, ENV):
        import deploy

        # Patch the module-level agent_registry and BrowserAgent
        with (
            patch.object(deploy, "agent_registry", fake_registry),
            patch.object(deploy, "BrowserAgent", return_value=fake_agent),
        ):
            yield TestClient(deploy.create_app())


@pytest.fixture()
def authed_client(client):
    """A client that has already logged in with the first token."""
    client.post("/login", data={"token": "alpha-token-aaa"})
    return client


# ---- Unauthenticated access ----


def test_unauthenticated_returns_401(client):
    assert client.get("/status").status_code == 401


def test_bearer_invalid(client):
    r = client.get("/status", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


# ---- Bearer token ----


def test_bearer_valid(client):
    r = client.get("/status", headers={"Authorization": "Bearer alpha-token-aaa"})
    assert r.status_code == 200
    assert r.json()["exit_code"] == 0


def test_bearer_second_token(client):
    r = client.get("/status", headers={"Authorization": "Bearer bravo-token-bbb"})
    assert r.status_code == 200


# ---- Login page ----


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Handheld" in r.text
    assert '<input type="password"' in r.text


def test_login_page_no_auth_required(client):
    # No cookies, no headers — should still get the page
    r = client.get("/login")
    assert r.status_code == 200


def test_login_valid_sets_cookie(client):
    r = client.post(
        "/login", data={"token": "alpha-token-aaa"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert "rodney_session" in r.headers.get("set-cookie", "")


def test_login_invalid_rejected(client):
    r = client.post("/login", data={"token": "wrong"})
    assert r.status_code == 401
    assert "invalid token" in r.text


# ---- Cookie auth ----


def test_cookie_auth_works(authed_client):
    r = authed_client.get("/status")
    assert r.status_code == 200


def test_expired_cookie_rejected(client):
    signer = URLSafeTimedSerializer(COOKIE_SECRET)
    signed = signer.dumps("alpha-token-aaa")
    # Verify it works when fresh
    client.cookies.set("rodney_session", signed)
    assert client.get("/status").status_code == 200
    # Now pretend 31 days have passed when the app checks the cookie
    real_time = time.time
    with patch("time.time", return_value=real_time() + 31 * 24 * 3600):
        client.cookies.set("rodney_session", signed)
        assert client.get("/status").status_code == 401


def test_tampered_cookie_rejected(client):
    client.cookies.set("rodney_session", "garbage.not.a.real.cookie")
    assert client.get("/status").status_code == 401


def test_revoked_token_cookie_fails():
    """Login with bravo token, then remove it from valid set — cookie should stop working."""
    fake_registry = FakeRegistry()
    fake_agent = _make_fake_agent()

    with patch.dict(os.environ, ENV):
        import deploy

        with (
            patch.object(deploy, "agent_registry", fake_registry),
            patch.object(deploy, "BrowserAgent", return_value=fake_agent),
        ):
            client = TestClient(deploy.create_app())
            client.post("/login", data={"token": "bravo-token-bbb"})
            assert client.get("/status").status_code == 200

    # Recreate app without bravo token
    revoked_env = {**ENV, "RODNEY_API_TOKENS": "alpha-token-aaa"}
    fake_registry2 = FakeRegistry()
    with patch.dict(os.environ, revoked_env):
        import deploy

        with (
            patch.object(deploy, "agent_registry", fake_registry2),
            patch.object(deploy, "BrowserAgent", return_value=fake_agent),
        ):
            new_client = TestClient(deploy.create_app())
            # Copy the cookie from old client
            new_client.cookies = client.cookies
            assert new_client.get("/status").status_code == 401


# ---- Public routes ----


def test_docs_public(client):
    r = client.get("/docs")
    assert r.status_code == 200


def test_openapi_public(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "paths" in r.json()


# ---- Logout ----


def test_logout_clears_cookie(authed_client):
    r = authed_client.get("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    cookie_header = r.headers.get("set-cookie", "")
    assert "rodney_session" in cookie_header
    # After logout, hitting a protected endpoint should fail
    # (clear the cookie on the client side to simulate browser behavior)
    authed_client.cookies.clear()
    assert authed_client.get("/status").status_code == 401


# ---- Every protected endpoint rejects without auth ----


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/status"),
        ("GET", "/screenshot"),
        ("GET", "/url"),
        ("GET", "/title"),
        ("POST", "/open?url=https://example.com"),
        ("POST", "/js?expression=1"),
        ("POST", "/run"),
    ],
)
def test_all_protected_endpoints_require_auth(client, method, path):
    r = client.request(method, path, json={"args": ["status"]} if path == "/run" else None)
    assert r.status_code == 401, f"{method} {path} should be 401, got {r.status_code}"


# ---- Agent lifecycle ----


def test_create_agent(authed_client):
    r = authed_client.post("/agents", json={"purpose": "test agent"})
    assert r.status_code == 200
    assert "agent_id" in r.json()


def test_list_agents_empty(authed_client):
    r = authed_client.get("/agents")
    assert r.status_code == 200
    assert r.json() == []


def test_list_agents_after_create(authed_client):
    authed_client.post("/agents", json={"purpose": "test"})
    r = authed_client.get("/agents")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_delete_agent(authed_client):
    r = authed_client.post("/agents", json={})
    agent_id = r.json()["agent_id"]
    r = authed_client.delete(f"/agents/{agent_id}")
    assert r.status_code == 200
    assert r.json()["deleted"] == agent_id
    # Should be gone
    r = authed_client.get(f"/agents/{agent_id}")
    assert r.status_code == 404


def test_delete_nonexistent_agent(authed_client):
    r = authed_client.delete("/agents/nonexistent")
    assert r.status_code == 404


def test_agents_summary(authed_client):
    authed_client.post("/agents", json={"purpose": "agent1"})
    r = authed_client.get("/agents/summary")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert "id" in data[0]
    assert "status" in data[0]


# ---- Scoped agent endpoints require auth ----


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/agents/test123/status"),
        ("GET", "/agents/test123/screenshot"),
        ("GET", "/agents/test123/url"),
        ("GET", "/agents/test123/title"),
        ("POST", "/agents/test123/open?url=https://example.com"),
        ("POST", "/agents/test123/js?expression=1"),
        ("POST", "/agents/test123/run"),
    ],
)
def test_scoped_endpoints_require_auth(client, method, path):
    r = client.request(method, path, json={"args": ["status"]} if path.endswith("/run") else None)
    assert r.status_code == 401, f"{method} {path} should be 401, got {r.status_code}"
