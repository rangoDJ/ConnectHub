"""Account sign-up and password changes through the API."""
import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Fresh auth + main against an empty CONFIG_DIR, in basic mode with no accounts."""
    os.environ["CONFIG_DIR"] = str(tmp_path)
    for name in ("auth", "main"):
        sys.modules.pop(name, None)
    main = importlib.import_module("main")
    auth = sys.modules["auth"]
    monkeypatch.setattr(auth, "AUTH_MODE", "basic")
    monkeypatch.setattr(main, "AUTH_MODE", "basic")
    auth._failed_logins.clear()
    return main, auth


def signup(main, username="alice", password="alice-password"):
    client = TestClient(main.app)
    return client, client.post("/auth/signup", json={"username": username, "password": password})


def login(main, username="alice", password="alice-password"):
    client = TestClient(main.app)
    res = client.post("/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return client


def change(client, current, new):
    return client.post("/api/account/password", json={"current_password": current, "new_password": new})


# ----------------- Sign-up -----------------

def test_status_reports_setup_on_a_fresh_install(app):
    main, _ = app
    data = TestClient(main.app).get("/auth/status").json()
    assert data["setup_required"] and data["signups_open"]


def test_first_signup_logs_the_user_in(app):
    main, _ = app
    client, res = signup(main)
    assert res.status_code == 200
    assert client.get("/api/session/status").status_code == 200
    assert TestClient(main.app).get("/auth/status").json()["setup_required"] is False


def test_signup_disabled_after_the_first_account(app, monkeypatch):
    main, auth = app
    monkeypatch.setattr(auth, "ALLOW_SIGNUPS", False)
    assert signup(main)[1].status_code == 200
    assert signup(main, "bob", "bob-password")[1].status_code == 403
    assert TestClient(main.app).get("/auth/status").json()["signups_open"] is False


def test_duplicate_username_is_409(app):
    main, _ = app
    signup(main)
    assert signup(main, "ALICE", "other-password")[1].status_code == 409


@pytest.mark.parametrize("username", ["", "has space", "a/b", "x" * 65, "<script>"])
def test_invalid_usernames_are_rejected(app, username):
    main, _ = app
    assert signup(main, username)[1].status_code == 422


def test_short_signup_password_is_rejected(app):
    main, _ = app
    assert signup(main, "alice", "short")[1].status_code == 422


def test_signup_not_available_outside_basic_mode(app, monkeypatch):
    main, auth = app
    monkeypatch.setattr(auth, "AUTH_MODE", "none")
    monkeypatch.setattr(main, "AUTH_MODE", "none")
    assert signup(main)[1].status_code == 400


# ----------------- Password change -----------------

def test_change_password_then_log_in_with_it(app):
    main, _ = app
    client, _ = signup(main)
    assert change(client, "alice-password", "alice-password-2").status_code == 200
    login(main, password="alice-password-2")
    res = TestClient(main.app).post("/auth/login", json={"username": "alice", "password": "alice-password"})
    assert res.status_code == 401


def test_wrong_current_password_is_403_not_401(app):
    """401 would make the frontend treat it as an expired session and redirect."""
    main, _ = app
    client, _ = signup(main)
    assert change(client, "not-the-password", "alice-password-2").status_code == 403


def test_wrong_current_password_counts_toward_the_rate_limit(app):
    main, auth = app
    client, _ = signup(main)
    for _ in range(auth.LOGIN_MAX_FAILURES):
        change(client, "guess", "alice-password-2")
    assert change(client, "alice-password", "alice-password-2").status_code == 429


def test_same_password_is_rejected(app):
    main, _ = app
    client, _ = signup(main)
    assert change(client, "alice-password", "alice-password").status_code == 400


def test_change_signs_out_that_users_other_sessions_only(app):
    main, _ = app
    changer, _ = signup(main)
    other_alice = login(main)
    signup(main, "bob", "bob-password")
    bob = login(main, "bob", "bob-password")
    assert change(changer, "alice-password", "alice-password-2").status_code == 200
    assert changer.get("/api/session/status").status_code == 200
    assert other_alice.get("/api/session/status").status_code == 401
    assert bob.get("/api/session/status").status_code == 200


def test_session_ends_when_the_account_is_removed(app):
    main, auth = app
    client, _ = signup(main)
    auth._users = {}
    assert client.get("/api/session/status").status_code == 401


def test_change_requires_a_session(app):
    main, _ = app
    signup(main)
    assert change(TestClient(main.app), "alice-password", "alice-password-2").status_code == 401
