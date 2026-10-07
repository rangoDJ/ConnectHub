"""Profile secret masking and display-scale resolution."""
import pytest

import main


# ----------------- Secret masking -----------------

def test_mask_is_swapped_back_for_the_stored_secret():
    """The UI echoes the mask back; sending it to FreeRDP as a password would break login."""
    data = {"password": main.PASSWORD_MASK, "ssh_key": main.PASSWORD_MASK}
    main.restore_masked_secrets(data, {"password": "real-pw", "ssh_key": "real-key"})
    assert data == {"password": "real-pw", "ssh_key": "real-key"}


def test_a_genuinely_edited_secret_is_kept():
    data = {"password": "new-pw", "ssh_key": main.PASSWORD_MASK}
    main.restore_masked_secrets(data, {"password": "old-pw", "ssh_key": "old-key"})
    assert data["password"] == "new-pw"
    assert data["ssh_key"] == "old-key"


def test_mask_with_no_stored_profile_becomes_empty():
    """A mask with nothing behind it must not be sent on as a literal password."""
    data = {"password": main.PASSWORD_MASK, "ssh_key": main.PASSWORD_MASK}
    main.restore_masked_secrets(data, None)
    assert data == {"password": "", "ssh_key": ""}


def test_missing_stored_field_becomes_empty():
    data = {"password": main.PASSWORD_MASK}
    main.restore_masked_secrets(data, {})
    assert data["password"] == ""


# ----------------- Desktop scale -----------------

@pytest.mark.parametrize("scale, dpr, expected", [
    ("150", None, 150),      # explicit setting wins
    ("150", 2.0, 150),       # ...even when the browser reports something else
    ("auto", None, 100),     # no hint from the browser
    ("auto", 1.0, 100),
    ("auto", 1.25, 125),
    ("auto", 1.5, 150),
    ("auto", 2.0, 200),
    ("auto", 1.1, 100),      # rounded to the nearest 25%
    ("auto", 1.2, 125),
    ("auto", 0.5, 100),      # clamped to the 100 floor
    ("auto", 5.0, 500),      # and the 500 ceiling
    (None, 2.0, 200),        # missing setting behaves like "auto"
    ("auto", 0, 100),        # falsy ratio is treated as unknown
])
def test_resolve_desktop_scale(scale, dpr, expected):
    assert main.resolve_desktop_scale(scale, dpr) == expected


def test_resolve_desktop_scale_always_lands_on_a_multiple_of_25():
    for i in range(5, 51):
        assert main.resolve_desktop_scale("auto", i / 10) % 25 == 0


# ----------------- Profile lookup -----------------

def test_find_profile():
    profiles = [{"id": "a", "host": "one"}, {"id": "b", "host": "two"}]
    assert main.find_profile(profiles, "b")["host"] == "two"
    assert main.find_profile(profiles, "missing") is None
    assert main.find_profile(profiles, None) is None
    assert main.find_profile([], "a") is None


# ----------------- Profile validation -----------------

@pytest.mark.parametrize("host", [
    "192.168.1.10",
    "pc.local",
    "server-01.example.com",
    "[2001:db8::1]",
])
def test_valid_hosts_are_accepted(host):
    assert main.ConnectionProfile(host=host).host == host


@pytest.mark.parametrize("bad_host", [
    "-oProxyCommand=evil",   # must never parse as a client option
    "",
    "host with spaces",
    "host\nname",
])
def test_invalid_hosts_are_rejected(bad_host):
    with pytest.raises(Exception):
        main.ConnectionProfile(host=bad_host)


@pytest.mark.parametrize("field", ["username", "password", "domain", "name"])
def test_line_breaks_are_rejected_in_single_line_fields(field):
    """FreeRDP's /args-from:stdin is newline-delimited, so a newline would inject an arg."""
    with pytest.raises(Exception):
        main.ConnectionProfile(host="1.2.3.4", **{field: "value\ninjected"})


def test_unknown_protocol_is_rejected():
    with pytest.raises(Exception):
        main.ConnectionProfile(host="1.2.3.4", protocol="telnet")


def test_port_bounds():
    assert main.ConnectionProfile(host="1.2.3.4", port=65535).port == 65535
    with pytest.raises(Exception):
        main.ConnectionProfile(host="1.2.3.4", port=0)
    with pytest.raises(Exception):
        main.ConnectionProfile(host="1.2.3.4", port=65536)


def test_port_defaults_to_none_for_the_protocol_default():
    assert main.ConnectionProfile(host="1.2.3.4").port is None


# ----------------- Masked secrets stay with their host -----------------

@pytest.fixture
def api(tmp_path, monkeypatch):
    """The API in no-auth mode with an empty profile store; connects are recorded, not run."""
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "PROFILES_FILE", tmp_path / "profiles.json")
    monkeypatch.setattr(main, "AUTH_MODE", "none")
    import auth
    monkeypatch.setattr(auth, "AUTH_MODE", "none")
    connects = []
    monkeypatch.setattr(main.session_manager, "connect",
                        lambda config: connects.append(config) or {"success": True, "message": "ok"})
    client = TestClient(main.app)
    profile_id = client.post("/api/profiles", json={
        "protocol": "rdp", "host": "10.0.0.5", "username": "kodi", "password": "real-pw",
    }).json()["id"]
    return client, profile_id, connects


def masked(profile_id, **changes):
    return {"id": profile_id, "protocol": "rdp", "host": "10.0.0.5", "username": "kodi",
            "password": main.PASSWORD_MASK, **changes}


def test_connect_reuses_the_password_for_the_same_host(api):
    client, profile_id, connects = api
    res = client.post("/api/session/connect", json={"custom": masked(profile_id, resolution="1920x1080")})
    assert res.status_code == 200
    assert connects[-1]["password"] == "real-pw"


@pytest.mark.parametrize("changes", [
    {"host": "attacker.example"},
    {"host": "10.0.0.6"},
    {"port": 3390},
    {"protocol": "vnc"},
])
def test_connect_refuses_a_masked_password_for_another_target(api, changes):
    """Otherwise any dashboard user could send a saved password to their own machine."""
    client, profile_id, connects = api
    res = client.post("/api/session/connect", json={"custom": masked(profile_id, **changes)})
    assert res.status_code == 400
    assert connects == []


def test_saving_a_new_host_with_the_masked_password_is_refused(api):
    client, profile_id, _ = api
    res = client.post("/api/profiles", json=masked(profile_id, host="attacker.example"))
    assert res.status_code == 400
    stored = main.find_profile(main.load_profiles(), profile_id)
    assert stored["host"] == "10.0.0.5" and stored["password"] == "real-pw"


def test_a_new_host_with_a_new_password_is_saved(api):
    client, profile_id, _ = api
    res = client.post("/api/profiles", json=masked(profile_id, host="10.0.0.9", password="new-pw"))
    assert res.status_code == 200
    stored = main.find_profile(main.load_profiles(), profile_id)
    assert stored["host"] == "10.0.0.9" and stored["password"] == "new-pw"


def test_host_case_and_the_default_port_count_as_the_same_target(api):
    client, profile_id, connects = api
    res = client.post("/api/session/connect", json={"custom": masked(profile_id, host="10.0.0.5", port=3389)})
    assert res.status_code == 200
    assert main.same_target({"host": "PC.local"}, {"host": "pc.local", "port": 3389})
