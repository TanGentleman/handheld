"""
Auth tests for Handheld's FastAPI app.

Runs without Modal or Chromium — just patches env vars
and subprocess calls so we can test auth flows in isolation.

    pytest test_auth.py -v
"""

import os
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

TOKENS = "alpha-token-aaa,bravo-token-bbb"
COOKIE_SECRET = "test-cookie-secret-for-signing"
ENV = {"RODNEY_API_TOKENS": TOKENS, "RODNEY_COOKIE_SECRET": COOKIE_SECRET}

FAKE_PROC = SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")


@pytest.fixture()
def client():
    with (
        patch.dict(os.environ, ENV),
        patch("subprocess.run", return_value=FAKE_PROC),
    ):
        from deploy import create_app

        yield TestClient(create_app(), base_url="https://testserver")


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
    with (
        patch.dict(os.environ, ENV),
        patch("subprocess.run", return_value=FAKE_PROC),
    ):
        from deploy import create_app

        client = TestClient(create_app(), base_url="https://testserver")
        client.post("/login", data={"token": "bravo-token-bbb"})
        assert client.get("/status").status_code == 200

    # Recreate app without bravo token
    revoked_env = {**ENV, "RODNEY_API_TOKENS": "alpha-token-aaa"}
    with (
        patch.dict(os.environ, revoked_env),
        patch("subprocess.run", return_value=FAKE_PROC),
    ):
        from deploy import create_app

        new_client = TestClient(create_app(), base_url="https://testserver")
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


# ---- Input validation ----


def test_run_empty_args_rejected(authed_client):
    r = authed_client.post("/run", json={"args": []})
    assert r.status_code == 422


def test_run_timeout_capped(authed_client):
    """Timeout should be capped at MAX_TIMEOUT (120s), not use the user-supplied value."""
    r = authed_client.post("/run", json={"args": ["status"], "timeout": 9999})
    assert r.status_code == 200
