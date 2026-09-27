"""Changing the basic auth password through the API."""
import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Fresh auth + main against an empty CONFIG_DIR, in basic mode with an env password."""
    os.environ["CONFIG_DIR"] = str(tmp_path)
    for name in ("auth", "main"):
        sys.modules.pop(name, None)
    main = importlib.import_module("main")
    auth = sys.modules["auth"]
    monkeypatch.setattr(auth, "AUTH_MODE", "basic")
    monkeypatch.setattr(main, "AUTH_MODE", "basic")
    monkeypatch.setattr(auth, "BASIC_AUTH_USER", "admin")
    monkeypatch.setattr(auth, "BASIC_AUTH_PASSWORD", "old-password")
    auth._failed_logins.clear()
    return main, auth


def login(main, password="old-password"):
    client = TestClient(main.app)
    res = client.post("/auth/login", json={"username": "admin", "password": password})
    assert res.status_code == 200, res.text
    return client


def change(client, current, new):
    return client.post("/api/account/password", json={"current_password": current, "new_password": new})


def test_change_password_then_log_in_with_it(app):
    main, _ = app
    client = login(main)
    assert change(client, "old-password", "new-password-1").status_code == 200
    login(main, "new-password-1")
    res = TestClient(main.app).post("/auth/login", json={"username": "admin", "password": "old-password"})
    assert res.status_code == 401


def test_wrong_current_password_is_403_not_401(app):
    """401 would make the frontend treat it as an expired session and redirect."""
    main, _ = app
    res = change(login(main), "not-the-password", "new-password-1")
    assert res.status_code == 403


def test_wrong_current_password_counts_toward_the_rate_limit(app):
    main, auth = app
    client = login(main)
    for _ in range(auth.LOGIN_MAX_FAILURES):
        change(client, "guess", "new-password-1")
    assert change(client, "old-password", "new-password-1").status_code == 429


def test_short_password_is_rejected(app):
    main, _ = app
    assert change(login(main), "old-password", "short").status_code == 422


def test_same_password_is_rejected(app):
    main, _ = app
    assert change(login(main), "old-password", "old-password").status_code == 400


def test_change_signs_out_other_sessions_but_not_this_one(app):
    main, _ = app
    changer, other = login(main), login(main)
    assert change(changer, "old-password", "new-password-1").status_code == 200
    assert changer.get("/api/session/status").status_code == 200
    assert other.get("/api/session/status").status_code == 401


def test_requires_a_session(app):
    main, _ = app
    res = change(TestClient(main.app), "old-password", "new-password-1")
    assert res.status_code == 401


def test_not_available_outside_basic_mode(app, monkeypatch):
    main, auth = app
    client = login(main)
    monkeypatch.setattr(auth, "AUTH_MODE", "none")
    monkeypatch.setattr(main, "AUTH_MODE", "none")
    assert change(client, "old-password", "new-password-1").status_code == 400
