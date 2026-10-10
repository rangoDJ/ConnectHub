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


@pytest.fixture
def open_signups(app, monkeypatch):
    monkeypatch.setattr(app[1], "ALLOW_SIGNUPS", True)


def test_signup_disabled_after_the_first_account_by_default(app):
    main, _ = app
    assert signup(main)[1].status_code == 200
    assert signup(main, "bob", "bob-password")[1].status_code == 403
    assert TestClient(main.app).get("/auth/status").json()["signups_open"] is False


def test_second_signup_works_when_enabled(app, open_signups):
    main, _ = app
    signup(main)
    assert signup(main, "bob", "bob-password")[1].status_code == 200


def test_duplicate_username_is_409(app, open_signups):
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


def test_change_signs_out_that_users_other_sessions_only(app, open_signups):
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


# ----------------- Logout -----------------

def test_logout_by_get_is_refused(app):
    """Another site could otherwise sign the user out just by linking here."""
    main, _ = app
    res = TestClient(main.app).get("/auth/logout", follow_redirects=False)
    assert res.status_code == 405


def test_logout_by_post_clears_the_session(app):
    main, auth = app
    client, res = signup(main)
    assert res.status_code == 200
    res = client.post("/auth/logout", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login.html"
    assert auth.COOKIE_NAME in res.headers.get("set-cookie", "")
    assert client.get("/auth/status").json()["authenticated"] is False


# ----------------- Server-side sessions -----------------

def test_a_copied_cookie_stops_working_after_logout(app):
    """Logging out ends the session, not just this browser's copy of the cookie."""
    main, auth = app
    client, res = signup(main)
    cookie = client.cookies.get(auth.COOKIE_NAME)
    copy = TestClient(main.app, cookies={auth.COOKIE_NAME: cookie})
    assert copy.get("/auth/status").json()["authenticated"] is True
    client.post("/auth/logout", follow_redirects=False)
    assert copy.get("/auth/status").json()["authenticated"] is False


def test_logging_out_one_session_keeps_the_others(app):
    main, auth = app
    signup(main)
    laptop, phone = login(main), login(main)
    laptop.post("/auth/logout", follow_redirects=False)
    assert phone.get("/auth/status").json()["authenticated"] is True


def test_sessions_survive_a_restart(app):
    main, auth = app
    client, _ = signup(main)
    cookie = client.cookies.get(auth.COOKIE_NAME)
    for name in ("auth", "main"):
        sys.modules.pop(name, None)
    main2 = importlib.import_module("main")
    sys.modules["auth"].AUTH_MODE = main2.AUTH_MODE = "basic"
    copy = TestClient(main2.app, cookies={auth.COOKIE_NAME: cookie})
    assert copy.get("/auth/status").json()["authenticated"] is True


def test_a_validly_signed_cookie_without_a_session_is_rejected(app):
    """Cookies issued before sessions had ids; also any cookie whose session ended."""
    main, auth = app
    signup(main)
    token = auth.serializer.dumps({"username": "alice", "auth_mode": "basic",
                                   "pwv": auth.user_epoch("alice")})
    client = TestClient(main.app, cookies={auth.COOKIE_NAME: token})
    assert client.get("/auth/status").json()["authenticated"] is False


def test_expired_sessions_are_dropped_from_the_file(app, monkeypatch):
    main, auth = app
    signup(main)
    real_time = auth.time.time
    monkeypatch.setattr(auth.time, "time", lambda: real_time() + auth.MAX_AGE + 60)
    login(main)
    assert len(auth._load_sessions()) == 1  # only the new one


def test_a_damaged_sessions_file_means_no_sessions(app):
    main, auth = app
    auth.SESSIONS_FILE.write_text("{not json")
    assert auth._load_sessions() == {}
