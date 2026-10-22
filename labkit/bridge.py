"""Client side of the harness's file bridge (<profile>/Lua/pzlab/).

cmd.txt  <- "PZLAB <seq> <timeoutMs>\\n<lua chunk>"   (written atomically, removed after reply)
res.json -> {"seq", "ok", "value"|"error", "out", "frames", "ms"}
state.json -> heartbeat every ~0.5 s: {"state": "menu"|"ingame", "t", "ticks", "busy", ...}
"""

import json
import os
import time


class LuaError(RuntimeError):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


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
        if state is not None:
            try:
                state["age_s"] = round(time.time() - path.stat().st_mtime, 2)
            except FileNotFoundError:
                return None
        return state

    def alive(self, max_age_s=3.0):
        state = self.state()
        return state is not None and state["age_s"] <= max_age_s

    def exec(self, code, timeout_ms=10000):
        """Runs a Lua chunk in the game; returns the raw result dict (ok/value/error/out)."""
        seq = str(time.time_ns())
        tmp = self.dir / "cmd.tmp"
        tmp.write_text("PZLAB %s %d\n%s" % (seq, timeout_ms, code), encoding="utf-8")
        os.replace(tmp, self.dir / "cmd.txt")
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
            try:
                (self.dir / "cmd.txt").unlink()
            except FileNotFoundError:
                pass

    def lua(self, code, timeout_ms=10000):
        """Runs a Lua chunk and returns its value; raises LuaError on failure."""
        res = self.exec(code, timeout_ms)
        if not res.get("ok"):
            raise LuaError(res.get("error") or "unknown error", res)
        return res.get("value")
