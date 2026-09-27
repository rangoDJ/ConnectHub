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


# ----------------- User accounts -----------------

def test_password_hash_round_trips(auth):
    stored = auth.hash_password("correct horse")
    assert stored.startswith("scrypt$")
    assert auth.verify_password_hash("correct horse", stored)
    assert not auth.verify_password_hash("wrong horse", stored)


def test_same_password_gets_a_new_salt(auth):
    assert auth.hash_password("same") != auth.hash_password("same")


@pytest.mark.parametrize("stored", ["", "plain", "md5$1$2$3$4$5", "scrypt$x$8$1$AAAA$AAAA", "scrypt$16384$8$1$!!$!!"])
def test_malformed_hash_never_verifies(auth, stored):
    assert not auth.verify_password_hash("anything", stored)


def test_fresh_install_has_no_account(auth):
    """No default account: nothing can log in until the first one is created."""
    assert auth.setup_required()
    assert auth.signups_open()
    assert not auth.check_basic_credentials("admin", "")
    assert not auth.check_basic_credentials("admin", "admin")


def test_create_user_then_log_in(auth):
    auth.create_user("alice", "alice-password")
    assert not auth.setup_required()
    assert auth.check_basic_credentials("alice", "alice-password")
    assert not auth.check_basic_credentials("alice", "wrong-password")
    assert not auth.check_basic_credentials("bob", "alice-password")


@pytest.fixture
def open_signups(auth, monkeypatch):
    monkeypatch.setattr(auth, "ALLOW_SIGNUPS", True)


def test_signups_are_closed_by_default(auth):
    assert auth.ALLOW_SIGNUPS is False
    auth.create_user("alice", "alice-password")
    assert not auth.signups_open()
    with pytest.raises(PermissionError):
        auth.create_user("bob", "bob-password")


def test_usernames_are_unique_ignoring_case(auth, open_signups):
    auth.create_user("alice", "alice-password")
    with pytest.raises(ValueError):
        auth.create_user("Alice", "other-password")


def test_first_account_is_allowed_even_with_signups_disabled(auth, monkeypatch):
    monkeypatch.setattr(auth, "ALLOW_SIGNUPS", False)
    assert auth.signups_open()
    auth.create_user("alice", "alice-password")
    assert not auth.signups_open()
    with pytest.raises(PermissionError):
        auth.create_user("bob", "bob-password")


def test_signups_stay_open_when_allowed(auth, open_signups):
    auth.create_user("alice", "alice-password")
    auth.create_user("bob", "bob-password")
    assert auth.check_basic_credentials("bob", "bob-password")


def test_accounts_survive_a_restart(tmp_path):
    first = fresh_auth(tmp_path)
    first.create_user("alice", "alice-password")
    second = fresh_auth(tmp_path)
    assert second.check_basic_credentials("alice", "alice-password")
    assert second.user_epoch("alice") == first.user_epoch("alice")


def test_users_file_is_private(auth, tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX permissions")
    auth.create_user("alice", "alice-password")
    assert (tmp_path / "users.json").stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "users.json.tmp").exists()


def test_password_is_not_stored_in_plain_text(auth, tmp_path):
    auth.create_user("alice", "alice-password")
    assert "alice-password" not in (tmp_path / "users.json").read_text()


def test_damaged_users_file_fails_closed(tmp_path):
    """A corrupt file must not look like a fresh install that anyone can claim."""
    (tmp_path / "users.json").write_text("{not json")
    auth = fresh_auth(tmp_path)
    assert not auth.setup_required()
    assert not auth.signups_open()
    with pytest.raises(PermissionError):
        auth.create_user("mallory", "mallory-password")


def test_password_change_rotates_only_that_users_epoch(auth, open_signups):
    auth.create_user("alice", "alice-password")
    auth.create_user("bob", "bob-password")
    alice, bob = auth.user_epoch("alice"), auth.user_epoch("bob")
    auth.set_user_password("alice", "alice-password-2")
    assert auth.user_epoch("alice") != alice
    assert auth.user_epoch("bob") == bob
    assert auth.check_basic_credentials("alice", "alice-password-2")
    assert not auth.check_basic_credentials("alice", "alice-password")


def test_changing_an_unknown_users_password_fails(auth):
    with pytest.raises(KeyError):
        auth.set_user_password("ghost", "ghost-password")
