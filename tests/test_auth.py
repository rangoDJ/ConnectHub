"""Session-secret persistence and login rate limiting."""
import importlib
import os
import sys

import pytest
from fastapi import HTTPException


def fresh_auth(config_dir):
    """Re-import auth against a clean CONFIG_DIR (the key is resolved at import time)."""
    os.environ["CONFIG_DIR"] = str(config_dir)
    os.environ.pop("SECRET_KEY", None)
    sys.modules.pop("auth", None)
    return importlib.import_module("auth")


@pytest.fixture
def auth(tmp_path):
    return fresh_auth(tmp_path)


# ----------------- Session secret -----------------

def test_empty_secret_file_does_not_become_the_key(tmp_path):
    """A truncated write must not leave an empty signing key, which anyone could forge."""
    (tmp_path / ".session_secret").write_text("")
    auth = fresh_auth(tmp_path)
    assert auth.SECRET_KEY
    assert len(auth.SECRET_KEY) == 64


def test_whitespace_only_secret_file_regenerates(tmp_path):
    (tmp_path / ".session_secret").write_text("   \n  ")
    auth = fresh_auth(tmp_path)
    assert len(auth.SECRET_KEY) == 64


def test_regenerated_secret_is_persisted(tmp_path):
    secret_file = tmp_path / ".session_secret"
    secret_file.write_text("")
    auth = fresh_auth(tmp_path)
    assert secret_file.read_text().strip() == auth.SECRET_KEY
    assert not (tmp_path / ".session_secret.tmp").exists(), "temp file was left behind"


def test_existing_secret_is_reused(tmp_path):
    """Sessions must survive a restart, so a valid stored key is never replaced."""
    known = "a" * 64
    (tmp_path / ".session_secret").write_text(known + "\n")
    assert fresh_auth(tmp_path).SECRET_KEY == known


def test_env_secret_wins_over_file(tmp_path):
    (tmp_path / ".session_secret").write_text("b" * 64)
    os.environ["CONFIG_DIR"] = str(tmp_path)
    os.environ["SECRET_KEY"] = "from-env"
    sys.modules.pop("auth", None)
    try:
        assert importlib.import_module("auth").SECRET_KEY == "from-env"
    finally:
        os.environ.pop("SECRET_KEY", None)


def test_session_cookie_round_trips(auth):
    token = auth.serializer.dumps({"username": "kodi", "auth_mode": "basic"})
    assert auth.serializer.loads(token)["username"] == "kodi"


# ----------------- Login rate limiting -----------------

@pytest.fixture
def clock(auth, monkeypatch):
    """Drive auth's view of time by hand; monkeypatch restores it afterwards."""
    now = [1_700_000_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    return now


def test_limit_blocks_at_max_failures(auth, clock):
    ip = "3.3.3.3"
    for _ in range(auth.LOGIN_MAX_FAILURES - 1):
        auth.record_login_failure(ip)
    auth.check_login_rate_limit(ip)  # one short of the limit: must not raise

    auth.record_login_failure(ip)
    with pytest.raises(HTTPException) as exc:
        auth.check_login_rate_limit(ip)
    assert exc.value.status_code == 429


def test_failures_expire_after_the_window(auth, clock):
    ip = "3.3.3.3"
    for _ in range(auth.LOGIN_MAX_FAILURES):
        auth.record_login_failure(ip)
    clock[0] += auth.LOGIN_WINDOW_SECONDS + 1
    auth.check_login_rate_limit(ip)  # window passed: the block lifts


def test_successful_login_clears_failures(auth, clock):
    ip = "3.3.3.3"
    for _ in range(auth.LOGIN_MAX_FAILURES):
        auth.record_login_failure(ip)
    auth.clear_login_failures(ip)
    auth.check_login_rate_limit(ip)


def test_dict_does_not_grow_without_bound(auth, clock):
    """Regression: only the looked-up IP used to be pruned, so a spray grew it forever."""
    for i in range(5000):
        auth.record_login_failure(f"10.0.{i // 256}.{i % 256}")
    assert len(auth._failed_logins) == 5000

    # every one of them goes quiet, then a single new failure arrives
    clock[0] += auth.LOGIN_WINDOW_SECONDS + auth.LOGIN_SWEEP_SECONDS + 1
    auth.record_login_failure("192.168.1.99")

    assert len(auth._failed_logins) == 1
    assert "192.168.1.99" in auth._failed_logins


def test_sweep_keeps_entries_still_inside_the_window(auth, clock):
    auth.record_login_failure("1.1.1.1")
    clock[0] += auth.LOGIN_WINDOW_SECONDS - 10
    auth.record_login_failure("2.2.2.2")  # triggers a sweep
    assert "1.1.1.1" in auth._failed_logins, "swept an entry that had not expired"


def test_sweep_does_not_reset_a_blocked_ip(auth, clock):
    """The sweep must never hand an attacker a fresh allowance."""
    ip = "3.3.3.3"
    for _ in range(auth.LOGIN_MAX_FAILURES):
        auth.record_login_failure(ip)
    clock[0] += auth.LOGIN_SWEEP_SECONDS + 1
    auth.record_login_failure("9.9.9.9")  # forces a sweep
    with pytest.raises(HTTPException):
        auth.check_login_rate_limit(ip)


def test_sweep_is_throttled(auth, clock):
    clock[0] += 10_000
    auth.record_login_failure("4.4.4.4")
    first = auth._last_sweep
    auth.record_login_failure("5.5.5.5")
    assert auth._last_sweep == first, "re-swept inside the throttle interval"

    clock[0] += auth.LOGIN_SWEEP_SECONDS + 1
    auth.record_login_failure("6.6.6.6")
    assert auth._last_sweep > first


def test_backwards_clock_jump_does_not_stall_sweeping(auth, clock):
    auth.record_login_failure("7.7.7.7")
    before = auth._last_sweep
    clock[0] -= 3600  # NTP steps the clock back
    auth.record_login_failure("8.8.8.8")
    assert auth._last_sweep < before, "sweeping stalled until the clock caught up"


# ----------------- Trusted proxy matching -----------------

@pytest.mark.parametrize("nets, ip, expected", [
    ("172.18.0.0/16", "172.18.0.5", True),
    ("172.18.0.0/16", "172.19.0.5", False),
    ("10.0.0.1", "10.0.0.1", True),
    ("10.0.0.1", "10.0.0.2", False),
    ("10.0.0.0/8, 192.168.1.0/24", "192.168.1.7", True),
    ("172.18.0.0/16", "", False),
    ("172.18.0.0/16", "not-an-ip", False),
    ("", "172.18.0.5", False),
])
def test_is_trusted_proxy(auth, monkeypatch, nets, ip, expected):
    monkeypatch.setattr(auth, "TRUSTED_PROXY_NETS", auth._parse_networks(nets))
    assert auth._is_trusted_proxy(ip) is expected


def test_parse_networks_skips_invalid_entries(auth):
    assert len(auth._parse_networks("10.0.0.0/8, nonsense, 192.168.1.0/24")) == 2


def test_check_basic_credentials(auth, monkeypatch):
    monkeypatch.setattr(auth, "BASIC_AUTH_USER", "admin")
    monkeypatch.setattr(auth, "BASIC_AUTH_PASSWORD", "hunter2")
    assert auth.check_basic_credentials("admin", "hunter2")
    assert not auth.check_basic_credentials("admin", "wrong")
    assert not auth.check_basic_credentials("root", "hunter2")


def test_empty_configured_password_rejects_everything(auth, monkeypatch):
    """AUTH_MODE=basic with no password set must not authenticate a blank password."""
    monkeypatch.setattr(auth, "BASIC_AUTH_PASSWORD", "")
    assert not auth.check_basic_credentials("admin", "")
