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
