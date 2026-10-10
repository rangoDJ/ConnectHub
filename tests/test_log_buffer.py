"""The Connection Logs panel's buffers, the Selkies log tail, and the quiet access log."""
import logging
import os

import pytest

from log_buffer import FileTail, LogBuffer


# ----------------- LogBuffer -----------------

def test_first_read_returns_everything_and_resets():
    buf = LogBuffer()
    buf.append("a")
    buf.append("b")
    out = buf.since(None)
    assert out == {"lines": ["a", "b"], "cursor": 2, "reset": True}


def test_later_reads_return_only_new_lines():
    buf = LogBuffer()
    buf.append("a")
    cursor = buf.since(None)["cursor"]
    buf.append("b")
    buf.append("c")
    out = buf.since(cursor)
    assert out["lines"] == ["b", "c"]
    assert out["reset"] is False
    assert buf.since(out["cursor"])["lines"] == []


def test_a_caught_up_reader_is_reset_by_clear():
    """The session log is cleared on connect; the panel must drop the old session's lines."""
    buf = LogBuffer()
    buf.append("old session")
    cursor = buf.since(None)["cursor"]
    buf.clear()
    out = buf.since(cursor)
    assert out["reset"] is True
    assert out["lines"] == []
    buf.append("new session")
    assert buf.since(out["cursor"]) == {"lines": ["new session"], "cursor": out["cursor"] + 1, "reset": False}


def test_a_reader_that_fell_behind_the_cap_is_reset():
    buf = LogBuffer(max_lines=3)
    buf.append("a")
    cursor = buf.since(None)["cursor"]
    for line in "bcdef":
        buf.append(line)
    out = buf.since(cursor)
    assert out["reset"] is True
    assert out["lines"] == ["d", "e", "f"]


def test_a_cursor_from_the_future_resets():
    """After a server restart the panel's cursor can be ahead of the new buffer."""
    buf = LogBuffer()
    buf.append("a")
    assert buf.since(50)["reset"] is True


# ----------------- FileTail -----------------

@pytest.fixture
def log_file(tmp_path):
    return tmp_path / "current"


def write(path, text, mode="ab"):
    with open(path, mode) as f:
        f.write(text.encode())


def test_missing_file_reports_unavailable(log_file):
    assert FileTail(str(log_file), LogBuffer()).poll() is False


def test_tail_follows_appended_lines(log_file):
    buf = LogBuffer()
    tail = FileTail(str(log_file), buf)
    write(log_file, "one\ntwo\n")
    assert tail.poll() is True
    write(log_file, "three\n")
    tail.poll()
    assert buf.lines() == ["one", "two", "three"]


def test_a_partial_line_waits_for_its_newline(log_file):
    buf = LogBuffer()
    tail = FileTail(str(log_file), buf)
    write(log_file, "INFO:ws:half")
    tail.poll()
    assert buf.lines() == []
    write(log_file, " a line\n")
    tail.poll()
    assert buf.lines() == ["INFO:ws:half a line"]


def test_first_poll_of_a_large_file_starts_at_a_line_boundary(log_file, monkeypatch):
    monkeypatch.setattr(FileTail, "INITIAL_BYTES", 20)
    write(log_file, "".join(f"line {i:03d}\n" for i in range(100)))
    buf = LogBuffer()
    FileTail(str(log_file), buf).poll()
    assert buf.lines()
    assert buf.lines()[-1] == "line 099"
    assert all(l.startswith("line ") and len(l) == 8 for l in buf.lines())


@pytest.mark.skipif(os.name == "nt", reason="Windows can't rename a file that is open")
def test_rotation_finishes_the_old_file_then_reads_the_new(log_file, tmp_path):
    """s6-log renames `current` away and starts a new one."""
    buf = LogBuffer()
    tail = FileTail(str(log_file), buf)
    write(log_file, "before\n")
    tail.poll()
    write(log_file, "last of old\n")
    os.replace(log_file, tmp_path / "@4000000000000000.s")
    write(log_file, "first of new\n", mode="wb")
    tail.poll()
    assert buf.lines() == ["before", "last of old", "first of new"]


def test_undecodable_bytes_do_not_break_the_tail(log_file):
    buf = LogBuffer()
    with open(log_file, "wb") as f:
        f.write(b"bad \xff byte\n")
    FileTail(str(log_file), buf).poll()
    assert buf.lines() == ["bad � byte"]


# ----------------- Access log filter -----------------

def access_record(path):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0,
                             '%s - "%s %s HTTP/%s" %d', ("1.2.3.4:0", "GET", path, "1.1", 200), None)


@pytest.mark.parametrize("path, logged", [
    ("/api/session/status", False),
    ("/api/logs?cursor=12", False),
    ("/api/logsx", True),
    ("/api/session/connect", True),
    ("/api/session/statusx", True),
    ("/auth/login", True),
])
def test_polling_requests_are_left_out_of_the_access_log(path, logged):
    import main
    assert main.QuietPollingFilter().filter(access_record(path)) is logged
