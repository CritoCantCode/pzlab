"""Client side of the harness's file bridge (<profile>/Lua/pzlab/).

cmd-<seq>.txt <- "PZLAB <seq> <timeoutMs>\\n<lua chunk>"   (written and closed first)
cmd-<seq>.go  <- empty flag, created after the command file is closed; both removed after the reply
res.json   -> {"seq", "ok", "value"|"error", "out", "frames", "ms"}
state.json -> heartbeat every ~0.5 s: {"state": "menu"|"ingame", "t", "ticks", "busy", ...}

The harness finds commands by listing the directory and opens a command file only once its flag
exists, so it never opens a file this side is writing, replacing or deleting (on Windows that
race fails with a sharing violation the game logs as SEVERE FileNotFoundException).
"""

import json
import re
import time

COMMAND_FILE = re.compile(r"^cmd-(\d+)\.(txt|go)$")
LEGACY_FILES = ("cmd.txt", "cmd.tmp")  # protocol before command flags


class LuaError(RuntimeError):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


def _unlink(path, attempts=20):
    """Deletes a file, retrying briefly while another process (a scanner) holds it."""
    for _ in range(attempts):
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except PermissionError:
            time.sleep(0.01)
    return False


class Bridge:
    def __init__(self, lua_dir):
        self.dir = lua_dir

    def _read_json(self, name):
        try:
            text = (self.dir / name).read_text(encoding="utf-8", errors="replace")
            return json.loads(text) if text.strip() else None
        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
            return None

    def state(self):
        """Latest heartbeat plus 'age_s' (seconds since it was written), or None."""
        path = self.dir / "state.json"
        state = self._read_json("state.json")
        for _ in range(5):  # caught mid-rewrite (empty or partial): the game finishes it within ms
            if state is not None or not path.exists():
                break
            time.sleep(0.01)
            state = self._read_json("state.json")
        if state is not None:
            try:
                state["age_s"] = round(time.time() - path.stat().st_mtime, 2)
            except FileNotFoundError:
                return None
        return state

    def alive(self, max_age_s=3.0):
        state = self.state()
        return state is not None and state["age_s"] <= max_age_s

    def _answered(self):
        """seq of the last reply the harness wrote (0 if none)."""
        res = self._read_json("res.json")
        try:
            return int(res["seq"]) if res else 0
        except (KeyError, TypeError, ValueError):
            return 0

    def _sweep(self, answered):
        """Removes command files the harness already answered (fire-and-forget leftovers) and
        files of the old single-file protocol. Never touches a pending command."""
        try:
            names = [p.name for p in self.dir.iterdir()]
        except FileNotFoundError:
            return
        for name in names:
            m = COMMAND_FILE.match(name)
            if (m and int(m.group(1)) <= answered) or name in LEGACY_FILES:
                _unlink(self.dir / name)

    def _post(self, code, timeout_ms):
        """Writes a command and its flag; returns (seq, command path, flag path)."""
        answered = self._answered()
        self._sweep(answered)
        seq = str(max(time.time_ns(), answered + 1))  # increasing even if the clock steps back
        cmd, flag = self.dir / ("cmd-%s.txt" % seq), self.dir / ("cmd-%s.go" % seq)
        cmd.write_text("PZLAB %s %d\n%s" % (seq, timeout_ms, code), encoding="utf-8")
        flag.write_bytes(b"")
        return seq, cmd, flag

    def exec(self, code, timeout_ms=10000):
        """Runs a Lua chunk in the game; returns the raw result dict (ok/value/error/out)."""
        seq, cmd, flag = self._post(code, timeout_ms)
        deadline = time.time() + timeout_ms / 1000 + 3
        try:
            while time.time() < deadline:
                res = self._read_json("res.json")
                if res is not None and str(res.get("seq")) == seq:
                    return res
                time.sleep(0.02)
            state = self.state()
            raise TimeoutError("no reply within %d ms (harness state: %s)" % (timeout_ms, state))
        finally:
            # Flag first: without it the harness never opens the command file.
            _unlink(flag)
            _unlink(cmd)

    def send(self, code):
        """Fire-and-forget command (no reply awaited, e.g. quitting). Its files are swept by the
        next exec once the harness has answered it."""
        return self._post(code, 60000)[0]

    def lua(self, code, timeout_ms=10000):
        """Runs a Lua chunk and returns its value; raises LuaError on failure."""
        res = self.exec(code, timeout_ms)
        if not res.get("ok"):
            raise LuaError(res.get("error") or "unknown error", res)
        return res.get("value")
