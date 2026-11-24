"""Claude Code sessions in pseudo-terminals, for the web UI's terminal panel.

The page starts one of the commands below (a fixed list, never text from the page) as its own
session, reads the terminal's output as a stream and sends the keystrokes back, so the candidate
answers the agent's questions in the browser exactly as in a terminal. Each command gets a new
session (a tab on the page), up to `MAX_RUNNING` at once; the output of every session is kept
(the newest `KEEP` bytes) until its tab is closed, so a reloaded page can show it again.

Needs a Unix pseudo-terminal: on Windows `available()` says so and the panel is left out.
"""

import os
import shutil
import signal
import struct
import subprocess
import threading

from jobfinder.paths import ROOT

try:
    import fcntl
    import pty
    import termios
except ImportError:  # Windows
    pty = None

KEEP = 512 * 1024
MAX_RUNNING = 6
MAX_SESSIONS = 12
CLAUDE = "claude"
COMMANDS = {  # name on the page -> arguments after `claude`
    "setup": ["/setup"],
    "create-profile": ["/create-profile"],
    "search": ["/search"],
    "write-cv": ["/write-cv"],
    "score": ["/execute-job-scoring"],
    "cv": ["/execute-cv"],
    "apply": ["/apply"],
    "track": ["/track"],
}


class TerminalError(Exception):
    pass


def available() -> str | None:
    """None when a session can start, else why not."""
    if pty is None:
        return "The terminal panel needs macOS or Linux."
    if shutil.which(CLAUDE) is None:
        return "Claude Code is not installed (the `claude` command was not found)."
    return None


class Session:
    def __init__(self, ident: str = "") -> None:
        self.id = ident
        self._lock = threading.Condition()
        self._buf = bytearray()
        self._start = 0  # absolute offset of _buf[0]
        self._proc: subprocess.Popen | None = None
        self._fd: int | None = None
        self.command: str | None = None
        self.exit_code: int | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self.exit_code is None

    def start(self, name: str, cols: int = 100, rows: int = 30) -> None:
        if name not in COMMANDS:
            raise TerminalError(f"unknown command {name!r}")
        reason = available()
        if reason:
            raise TerminalError(reason)
        with self._lock:
            if self.running:
                raise TerminalError(f"/{self.command} is still running: stop it first.")
            master, slave = pty.openpty()
            _resize(master, cols, rows)
            env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"}
            self._proc = subprocess.Popen([CLAUDE, *COMMANDS[name]], stdin=slave, stdout=slave, stderr=slave,
                                          cwd=ROOT, env=env, start_new_session=True, close_fds=True)
            os.close(slave)
            self._fd, self.command, self.exit_code = master, name, None
            self._buf.clear()
            self._start = 0
        threading.Thread(target=self._read, args=(self._proc, master), daemon=True).start()

    def _read(self, proc: subprocess.Popen, fd: int) -> None:
        while True:
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            if not data:
                break
            with self._lock:
                self._buf += data
                if len(self._buf) > KEEP:
                    cut = len(self._buf) - KEEP
                    del self._buf[:cut]
                    self._start += cut
                self._lock.notify_all()
        code = proc.wait()
        with self._lock:
            self.exit_code = code
            self._fd = None
            self._lock.notify_all()
        os.close(fd)

    def write(self, text: str) -> None:
        with self._lock:
            if not self.running or self._fd is None:
                raise TerminalError("no session is running")
            fd = self._fd
        os.write(fd, text.encode("utf-8"))

    def resize(self, cols: int, rows: int) -> None:
        with self._lock:
            if self.running and self._fd is not None:
                _resize(self._fd, cols, rows)

    def stop(self) -> None:
        with self._lock:
            proc = self._proc if self.running else None
        if proc is None:
            return
        for sig, wait in ((signal.SIGHUP, 2), (signal.SIGTERM, 3), (signal.SIGKILL, 3)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                return
            try:
                proc.wait(wait)
                return
            except subprocess.TimeoutExpired:
                continue

    def read_from(self, pos: int, timeout: float = 15.0) -> tuple[bytes, int, int | None]:
        """Output after absolute offset `pos`, waiting up to `timeout` seconds for some:
        (data, new offset, exit code once the session ended and everything was read)."""
        with self._lock:
            self._lock.wait_for(lambda: self._start + len(self._buf) > pos or self.exit_code is not None, timeout)
            pos = max(pos, self._start)
            data = bytes(self._buf[pos - self._start:])
            end = self._start + len(self._buf)
            return data, end, self.exit_code

    def info(self) -> dict:
        return {"id": self.id, "command": self.command, "running": self.running, "exit_code": self.exit_code}


class Sessions:
    """Every session of one server run, by id (`t1`, `t2`, ...)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, Session] = {}
        self._count = 0

    def start(self, name: str, cols: int = 100, rows: int = 30) -> Session:
        with self._lock:
            if sum(s.running for s in self._by_id.values()) >= MAX_RUNNING:
                raise TerminalError(f"{MAX_RUNNING} sessions are already running: stop one first.")
            if len(self._by_id) >= MAX_SESSIONS:
                raise TerminalError("Too many open tabs: close some first.")
            self._count += 1
            session = Session(f"t{self._count}")
            session.start(name, cols, rows)
            self._by_id[session.id] = session
            return session

    def get(self, ident) -> Session:
        session = self._by_id.get(ident) if isinstance(ident, str) else None
        if session is None:
            raise TerminalError("no such session")
        return session

    def close(self, ident) -> None:
        session = self.get(ident)
        session.stop()
        with self._lock:
            self._by_id.pop(session.id, None)

    def stop_all(self) -> None:
        for session in list(self._by_id.values()):
            session.stop()

    def status(self) -> dict:
        reason = available()
        return {"available": reason is None, "reason": reason, "sessions": [s.info() for s in self._by_id.values()]}


def _resize(fd: int, cols: int, rows: int) -> None:
    cols, rows = max(20, min(int(cols), 500)), max(5, min(int(rows), 200))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
