"""Log lines kept in memory for the dashboard's Logs window.

The panel shows the container's log file through a LogBuffer, polling it with the
cursor from its previous read to get only the lines added since, so a long log isn't
resent every two seconds.
"""
import os
import threading
from collections import deque
from typing import Any, Dict, List, Optional

DEFAULT_MAX_LINES = 1000


class LogBuffer:
    """A capped, thread-safe list of log lines that readers follow with a cursor."""

    def __init__(self, max_lines: int = DEFAULT_MAX_LINES):
        self.max_lines = max_lines
        self._lines: deque = deque()
        # Sequence number of the next line appended; never goes backwards
        self._next = 0
        self._lock = threading.Lock()

    def append(self, line: str):
        line = line.strip()
        if not line:
            return
        with self._lock:
            self._lines.append(line)
            self._next += 1
            while len(self._lines) > self.max_lines:
                self._lines.popleft()

    def clear(self):
        with self._lock:
            self._lines.clear()
            # Skip a sequence number so a reader that was fully caught up lands before
            # the first retained line and is told to reset, rather than keeping the
            # cleared lines on screen.
            self._next += 1

    def lines(self) -> List[str]:
        with self._lock:
            return list(self._lines)

    def since(self, cursor: Optional[int]) -> Dict[str, Any]:
        """Lines added after `cursor`. `reset` means the reader must drop what it has
        (first read, a cleared log, or lines it hadn't seen were already trimmed)."""
        with self._lock:
            first = self._next - len(self._lines)
            if cursor is None or cursor < first or cursor > self._next:
                return {"lines": list(self._lines), "cursor": self._next, "reset": True}
            skip = cursor - first
            return {"lines": [l for i, l in enumerate(self._lines) if i >= skip],
                    "cursor": self._next, "reset": False}


class FileTail:
    """Feeds the lines appended to a file into a LogBuffer, one poll() at a time.

    Made for s6-log's `current` file, which s6-log renames away when it rotates;
    the rest of the old file is read before moving on to the new one.
    """

    # How much of an existing file the first poll reads
    INITIAL_BYTES = 256 * 1024

    def __init__(self, path: str, buffer: LogBuffer):
        self.path = path
        self.buffer = buffer
        self._file = None
        self._inode: Optional[int] = None
        self._partial = b""
        self._lock = threading.Lock()

    def poll(self) -> bool:
        """Read what was appended since the last poll; False if the file doesn't exist."""
        with self._lock:
            try:
                st = os.stat(self.path)
            except OSError:
                return False
            try:
                if self._file is not None and (st.st_ino != self._inode or st.st_size < self._file.tell()):
                    # Rotated (or truncated): finish the old file, then start the new one
                    self._read_available()
                    self._close()
                if self._file is None:
                    self._open(st, first=self._inode is None)
                self._read_available()
            except OSError:
                self._close()
                return False
            return True

    def _open(self, st: os.stat_result, first: bool):
        self._file = open(self.path, "rb")
        self._inode = st.st_ino
        self._partial = b""
        if first and st.st_size > self.INITIAL_BYTES:
            self._file.seek(st.st_size - self.INITIAL_BYTES)
            self._file.readline()  # drop the line the seek landed in

    def _read_available(self):
        data = self._file.read()
        if not data:
            return
        lines = (self._partial + data).split(b"\n")
        # The last piece has no newline yet; hold it until the rest is written
        self._partial = lines.pop()
        for line in lines:
            self.buffer.append(line.decode("utf-8", errors="replace"))

    def _close(self):
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
        self._file = None
        self._partial = b""
