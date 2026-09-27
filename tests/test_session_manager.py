"""Command building and the window-detection watchdog."""
import pytest

import session_manager as sm


@pytest.fixture
def mgr(monkeypatch):
    manager = sm.SessionManager.__new__(sm.SessionManager)  # no binary probing
    manager.process = None
    manager.protocol = None
    manager.status = "disconnected"
    manager.last_error = None
    manager.log_history = []
    manager.max_logs = 100
    manager.current_target = None
    manager.start_time = None
    manager.lock = sm.threading.Lock()
    manager._user_disconnected = set()
    manager._pending_connect = None
    manager.rdp_binary = "xfreerdp"
    manager.supports_args_from = True
    return manager


# ----------------- RDP -----------------

def test_rdp_password_goes_over_stdin_not_argv(mgr):
    """argv is world-readable in /proc, so the password must not appear there."""
    launch = mgr._build_rdp({"host": "1.2.3.4", "port": 3389, "password": "hunter2"})
    assert "hunter2" not in " ".join(launch.cmd)
    assert "/p:hunter2" in launch.stdin_text


def test_rdp_falls_back_to_argv_without_args_from(mgr):
    mgr.supports_args_from = False
    launch = mgr._build_rdp({"host": "1.2.3.4", "port": 3389, "password": "hunter2"})
    assert launch.stdin_text is None
    assert "/p:hunter2" in launch.cmd


def test_rdp_rejects_newlines_in_args(mgr):
    """/args-from:stdin is newline-delimited, so a newline would inject a FreeRDP option."""
    with pytest.raises(sm.ConfigError):
        mgr._build_rdp({"host": "1.2.3.4", "port": 3389, "username": "a\n/drive:x,/"})


def test_rdp_target_and_optional_flags(mgr):
    launch = mgr._build_rdp({
        "host": "pc.local", "port": 3390,
        "enable_audio": False, "enable_clipboard": False,
        "enable_drive": False, "ignore_cert": False,
    })
    assert launch.target == "pc.local:3390"
    args = launch.stdin_text
    assert "/v:pc.local:3390" in args
    assert "/sound" not in args
    assert "+clipboard" not in args
    assert "/drive" not in args
    assert "/cert:ignore" not in args


def test_rdp_resolution_modes(mgr):
    dynamic = mgr._build_rdp({"host": "h", "port": 1, "resolution": "dynamic"})
    assert "/dynamic-resolution" in dynamic.stdin_text
    fixed = mgr._build_rdp({"host": "h", "port": 1, "resolution": "1920x1080"})
    assert "/size:1920x1080" in fixed.stdin_text


def test_rdp_scaling_only_applied_above_100(mgr):
    plain = mgr._build_rdp({"host": "h", "port": 1, "desktop_scale": 100})
    assert "/scale-desktop" not in plain.stdin_text
    scaled = mgr._build_rdp({"host": "h", "port": 1, "desktop_scale": 200})
    assert "/scale-desktop:200" in scaled.stdin_text


# ----------------- SSH -----------------

def test_ssh_password_goes_via_env_not_argv(mgr):
    launch = mgr._build_ssh({"host": "h", "port": 22, "password": "hunter2"})
    assert "hunter2" not in " ".join(launch.cmd)
    assert launch.env["SSHPASS"] == "hunter2"
    assert launch.cmd[launch.cmd.index("sshpass") + 1] == "-e"


def test_ssh_host_is_after_a_double_dash(mgr):
    """So a host beginning with '-' can never be read as an ssh flag."""
    launch = mgr._build_ssh({"host": "h", "port": 22})
    assert launch.cmd[launch.cmd.index("--") + 1] == "h"


def test_ssh_rejects_a_key_that_is_not_a_private_key(mgr):
    with pytest.raises(sm.ConfigError):
        mgr._build_ssh({"host": "h", "port": 22, "ssh_key": "ssh-rsa AAAAB3Nza..."})


def test_ssh_key_is_written_to_a_private_file(mgr):
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"
    launch = mgr._build_ssh({"host": "h", "port": 22, "ssh_key": key})
    key_path = launch.cmd[launch.cmd.index("-i") + 1]
    assert "IdentitiesOnly=yes" in launch.cmd
    with open(key_path) as f:
        assert f.read().startswith("-----BEGIN OPENSSH PRIVATE KEY-----")


# ----------------- Key injection -----------------

def test_only_known_key_actions_are_accepted(mgr):
    mgr.protocol = "rdp"
    assert not mgr.send_keys("rm -rf /")["success"]
    assert not mgr.send_keys("unknown_action")["success"]


def test_ssh_sessions_expose_no_key_actions(mgr):
    mgr.protocol = "ssh"
    assert not mgr.send_keys("ctrl_alt_del")["success"]


def test_rdp_maps_ctrl_alt_del_to_ctrl_alt_end():
    """FreeRDP translates Ctrl+Alt+End for the remote host."""
    assert sm.KEY_MAP["rdp"]["ctrl_alt_del"] == ["Control_L+Alt_L+End"]
    assert sm.KEY_MAP["vnc"]["ctrl_alt_del"] == ["Control_L+Alt_L+Delete"]


# ----------------- Connect guards -----------------

def test_unsupported_protocol_is_refused(mgr):
    assert not mgr.connect({"protocol": "telnet", "host": "h"})["success"]


def test_missing_host_is_refused(mgr):
    assert not mgr.connect({"protocol": "rdp", "host": ""})["success"]


# ----------------- Log ring buffer -----------------

def test_log_history_is_capped(mgr):
    mgr.max_logs = 10
    for i in range(50):
        mgr._append_log(f"line {i}")
    assert len(mgr.log_history) == 10
    assert mgr.log_history[-1] == "line 49"


def test_blank_log_lines_are_dropped(mgr):
    mgr._append_log("   \n")
    assert mgr.log_history == []


# ----------------- Window watchdog -----------------

class FakeProc:
    pid = 4242
    def poll(self):
        return None


def test_watchdog_gives_up_waiting_for_a_window(mgr, monkeypatch):
    """Regression: a window that never matches used to poll xdotool for the whole
    session and leave the status stuck on 'connecting' forever."""
    now = [1000.0]
    monkeypatch.setattr(sm.time, "time", lambda: now[0])
    monkeypatch.setattr(sm.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))

    calls = []
    monkeypatch.setattr(mgr, "_window_exists", lambda l, p: calls.append(1) or False)

    proc = FakeProc()
    mgr.process = proc
    mgr.status = "connecting"
    mgr._watch_connected(proc, sm.Launch(cmd=[], target="h:1", match_pid=True))

    assert mgr.status == "connected"
    assert mgr.start_time is not None
    # bounded by WINDOW_WAIT_SECONDS at one probe per 0.5s, not unbounded
    assert len(calls) <= sm.WINDOW_WAIT_SECONDS * 2 + 2


def test_watchdog_without_the_cap_would_poll_forever(mgr, monkeypatch):
    """Pins the regression: with the timeout removed, the loop never stops probing."""
    now = [1000.0]
    monkeypatch.setattr(sm.time, "time", lambda: now[0])
    monkeypatch.setattr(sm.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(sm, "WINDOW_WAIT_SECONDS", 10 ** 9)  # i.e. the old, uncapped code

    calls = []
    def probe(launch, proc):
        calls.append(1)
        if len(calls) > 500:
            raise RuntimeError("still polling")
        return False
    monkeypatch.setattr(mgr, "_window_exists", probe)

    proc = FakeProc()
    mgr.process = proc
    mgr.status = "connecting"
    with pytest.raises(RuntimeError):
        mgr._watch_connected(proc, sm.Launch(cmd=[], target="h:1", match_pid=True))
    assert mgr.status == "connecting", "would have stayed stuck on connecting"


def test_watchdog_marks_connected_as_soon_as_the_window_appears(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "_window_exists", lambda l, p: True)
    proc = FakeProc()
    mgr.process = proc
    mgr.status = "connecting"
    mgr._watch_connected(proc, sm.Launch(cmd=[], target="h:1"))
    assert mgr.status == "connected"


def test_watchdog_falls_back_when_xdotool_is_missing(mgr, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(sm.time, "time", lambda: now[0])
    monkeypatch.setattr(sm.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(mgr, "_window_exists", lambda l, p: None)

    proc = FakeProc()
    mgr.process = proc
    mgr.status = "connecting"
    mgr._watch_connected(proc, sm.Launch(cmd=[], target="h:1"))
    assert mgr.status == "connected"


def test_watchdog_leaves_a_superseded_session_alone(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "_window_exists", lambda l, p: True)
    proc = FakeProc()
    mgr.process = FakeProc()  # a newer session already replaced this one
    mgr.status = "connecting"
    mgr._watch_connected(proc, sm.Launch(cmd=[], target="h:1"))
    assert mgr.status == "connecting"


# ----------------- Waiting for the display to settle -----------------

@pytest.fixture
def clock(monkeypatch):
    """Fake time: sleep advances the clock instead of waiting."""
    now = [1000.0]
    monkeypatch.setattr(sm.time, "time", lambda: now[0])
    monkeypatch.setattr(sm.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(sm.shutil, "which", lambda name: "/usr/bin/xdotool")
    return now


def sizes_over_time(clock, timeline):
    """_display_size stand-in returning the last size whose start time has passed."""
    start = clock[0]
    def size():
        current = None
        for at, value in timeline:
            if clock[0] - start >= at:
                current = value
        return current
    return size


def test_waits_for_the_stream_to_resize_the_display(mgr, monkeypatch, clock):
    """The regression: the session must not start until Selkies has resized the display."""
    start = clock[0]
    monkeypatch.setattr(mgr, "_display_size", sizes_over_time(clock, [(0, (1024, 768)), (0.9, (2880, 1472))]))
    mgr._wait_for_display_to_settle()
    waited = clock[0] - start
    assert waited >= 0.9 + sm.DISPLAY_SETTLE_SECONDS
    assert waited < sm.DISPLAY_SETTLE_MAX_SECONDS


def test_already_sized_display_only_waits_the_settle_time(mgr, monkeypatch, clock):
    start = clock[0]
    monkeypatch.setattr(mgr, "_display_size", lambda: (2880, 1472))
    mgr._wait_for_display_to_settle()
    assert clock[0] - start == pytest.approx(sm.DISPLAY_SETTLE_SECONDS, abs=sm.DISPLAY_POLL_SECONDS)


def test_a_display_that_keeps_changing_is_capped(mgr, monkeypatch, clock):
    start = clock[0]
    counter = [0]
    def changing():
        counter[0] += 1
        return (1000 + counter[0], 800)
    monkeypatch.setattr(mgr, "_display_size", changing)
    mgr._wait_for_display_to_settle()
    assert clock[0] - start == pytest.approx(sm.DISPLAY_SETTLE_MAX_SECONDS, abs=sm.DISPLAY_POLL_SECONDS)


def test_no_wait_without_xdotool(mgr, monkeypatch):
    monkeypatch.setattr(sm.shutil, "which", lambda name: None)
    monkeypatch.setattr(mgr, "_display_size", lambda: pytest.fail("polled without xdotool"))
    mgr._wait_for_display_to_settle()


def test_status_is_connecting_while_waiting(mgr, monkeypatch):
    """The dashboard unloads the stream on 'disconnected', and the stream sizes the display."""
    seen = []
    monkeypatch.setattr(mgr, "_wait_for_display_to_settle", lambda: seen.append(mgr.status))
    monkeypatch.setattr(sm.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("no client")))
    mgr.connect({"protocol": "vnc", "host": "10.0.0.5"})
    assert seen == ["connecting"]


def test_disconnect_while_waiting_cancels_the_launch(mgr, monkeypatch):
    launched = []
    monkeypatch.setattr(mgr, "_wait_for_display_to_settle", lambda: mgr.disconnect())
    monkeypatch.setattr(sm.subprocess, "Popen", lambda *a, **k: launched.append(a))
    result = mgr.connect({"protocol": "vnc", "host": "10.0.0.5"})
    assert not result["success"]
    assert launched == []
    assert mgr.status == "disconnected"


def test_second_connect_while_one_is_starting_is_refused(mgr, monkeypatch):
    results = []
    monkeypatch.setattr(mgr, "_wait_for_display_to_settle",
                        lambda: results.append(mgr.connect({"protocol": "vnc", "host": "10.0.0.6"})))
    monkeypatch.setattr(sm.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("no client")))
    mgr.connect({"protocol": "vnc", "host": "10.0.0.5"})
    assert results and not results[0]["success"]
    assert "already starting" in results[0]["message"]
