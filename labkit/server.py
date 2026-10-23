"""Lab server: a dedicated Project Zomboid server (zombie.network.GameServer, from the installed
game, no Steam) for multiplayer tests. It binds to loopback, keeps everything in its own cachedir
(<profile>-server/ by default), runs lab.json's "world" (map, sandbox, spawn point) with the same
mods as the lab client, and loads the PZLab harness, so tests can run Lua on the server (the
authoritative world) as well as on clients. See AGENT.md "Multiplayer".

Console: the server reads commands from its stdin. The lab starts it with stdin opened on
<server profile>/pzlab-console.txt and appends a line per command (`command`); the server polls
stdin (BufferedReader.ready, every 100 ms), so lines appended later are read. Replies go to
stdout, i.e. <server profile>/stdout.log, which is also the server's log (`log`).
"""

import re
import time

from . import process, profile, win32
from .bridge import Bridge, LuaError
from .logs import Log

READY_LINE = "*** SERVER STARTED ****"
CONSOLE_INPUT = "pzlab-console.txt"
# GameServer echoes every console line: 'command entered via server console (System.in): "save"'
ECHO = "command entered via server console"


class Server:
    def __init__(self, cfg):
        self.cfg = cfg
        self.bridge = Bridge(cfg.server_lua_dir)
        self.log = Log(path=self.root / "stdout.log", mark_file=self.root / "pzlab.logmark")

    @property
    def root(self):
        return self.cfg.server_profile_dir

    @property
    def name(self):
        return self.cfg["server"]["name"]

    @property
    def address(self):
        server = self.cfg["server"]
        return {"ip": server["ip"], "port": server["port"]}

    # Lifecycle ------------------------------------------------------------------------------

    def pid(self):
        return process.running_pid_file(self.cfg.server_pid_file)

    def setup(self, enable=None, mod_dirs=None, world=None):
        """Prepares the server profile: mod links (the lab client's mods), Server/<name>.ini,
        sandbox and spawn files. Returns {profile, active_mods, options, world}."""
        cfg = self.cfg
        names = list(cfg["enabled"] if enable is None else enable)
        for path in mod_dirs or []:
            names.append(cfg.add_mod_dir(path))
        profile.ensure_server_dirs(cfg)
        profile.link_mods(cfg, cfg.server_profile_dir / "mods")
        ids = [profile.mod_id(cfg, n) if n in cfg.mods else n for n in names]
        ids = ["PZLabHarness"] + [i for i in ids if i != "PZLabHarness"]
        spec = dict(cfg["world"], **(world or {}))
        options = profile.write_server_files(cfg, ids, spec)
        return {"profile": str(self.root), "active_mods": ids, "world": spec,
                "address": self.address, "options": {k: options[k] for k in ("Map", "Mods", "SpawnPoint")}}

    def start(self, enable=None, mod_dirs=None, world=None, fresh=None, wait=True, timeout_s=300):
        """Starts the lab server. fresh (default: lab.json server.fresh, True) archives the
        previous world and user database first (Saves/Multiplayer/<name>_<time>/, keep_saves
        kept), so the world is new and every client creates a character. Waits until the server
        listens and its harness answers (wait=False: returns after launching).
        Returns {profile, active_mods, world, address, pid, archived, ready_s, ...}."""
        cfg = self.cfg
        if self.pid():
            raise RuntimeError("lab server already running (pid %s); `server stop` first" % self.pid())
        fresh = cfg["server"]["fresh"] if fresh is None else fresh
        info = self.setup(enable, mod_dirs, world)
        profile.clear_runtime_files(cfg, cfg.server_lua_dir)
        if fresh:
            info.update(profile.archive_server_world(cfg, cfg["keep_saves"]))
        console = self.root / CONSOLE_INPUT
        console.write_bytes(b"")
        started = time.time()
        info["pid"] = process.start_process(process.server_command(cfg), cfg.game_dir, cfg.server_pid_file,
                                            self.root / "stdout.log", stdin_path=console, what="lab server")
        if wait:
            info["state"] = self.wait_ready(timeout_s)
            info["ready_s"] = round(time.time() - started, 1)
        return info

    def wait_ready(self, timeout_s=300):
        """Waits until the server logs READY_LINE and its harness heartbeat is fresh."""
        deadline = time.time() + timeout_s
        listening = False
        while time.time() < deadline:
            if not self.pid():
                raise RuntimeError("lab server exited while starting. Last lines:\n%s"
                                   % "\n".join(self.log.lines(since=0)[-40:]))
            if not listening:
                listening = any(READY_LINE in line for line in self.log.lines(since=0))
            state = self.bridge.state()
            if listening and state and state.get("age_s", 99) < 3 and state.get("state") == "server":
                return state
            time.sleep(0.5)
        problem = ("never logged %r" % READY_LINE if not listening else
                   "harness never answered (is PZLabHarness in the server's Mods? heartbeat: %s)"
                   % self.bridge.state())
        raise TimeoutError("lab server not ready within %ds: %s" % (timeout_s, problem))

    def stop(self, force=False, timeout_s=60):
        """`quit` on the console (the server saves and exits), kill after timeout_s; force: kill."""
        pid = self.pid()
        if not pid:
            return {"stopped": False, "reason": "not running"}
        how = "killed"
        if not force:
            try:
                self.command("quit", timeout_s=0)
            except OSError:
                pass
            deadline = time.time() + timeout_s
            while time.time() < deadline and win32.process_alive(pid):
                time.sleep(0.5)
            if not win32.process_alive(pid):
                how = "quit"
        if win32.process_alive(pid):
            win32.terminate(pid)
            deadline = time.time() + 15
            while time.time() < deadline and win32.process_alive(pid):
                time.sleep(0.2)
        return {"stopped": not win32.process_alive(pid), "pid": pid, "how": how}

    def status(self):
        pid = self.pid()
        return {"running": bool(pid), "pid": pid, "address": self.address, "profile": str(self.root),
                "harness": self.bridge.state() if pid else None}

    # Console --------------------------------------------------------------------------------

    def command(self, text, timeout_s=10, quiet_s=0.5):
        """Runs a server console command (as typed in the server window: `players`, `save`,
        `adduser "name" "password"`, `setaccesslevel "name" admin`, `help`, ...). Returns the
        stdout lines that followed the server's echo of it, once output has been quiet for
        quiet_s (timeout_s=0: don't wait, return [])."""
        if "\n" in text or "\r" in text:
            raise ValueError("one command per call (no newlines)")
        if not self.pid():
            raise RuntimeError("lab server is not running")
        offset = self.log.size()
        with open(self.root / CONSOLE_INPUT, "ab") as f:
            f.write(text.encode("utf-8") + b"\n")
        if timeout_s <= 0:
            return []
        deadline = time.time() + timeout_s
        echoed, last_count, last_change = None, -1, time.time()
        while time.time() < deadline:
            lines = self.log.lines(since=offset)
            if echoed is None:
                for i, line in enumerate(lines):
                    if ECHO in line:
                        echoed = i
                        break
            if echoed is not None:
                if len(lines) != last_count:
                    last_count, last_change = len(lines), time.time()
                elif time.time() - last_change >= quiet_s:
                    return lines[echoed + 1:]
            time.sleep(0.1)
        if echoed is None:
            raise TimeoutError("the server did not read the command %r within %ss" % (text, timeout_s))
        return self.log.lines(since=offset)[echoed + 1:]

    def ensure_user(self, user, password, access="admin"):
        """Creates the account if missing and sets its access level (console `adduser`,
        `setaccesslevel`). A client started with -debug (the lab default) may only join with an
        account whose role has ConnectWithDebug (admin); the server's own admin account
        (lab.json server.admin_user) always has. Returns the console output."""
        out = self.command('adduser "%s" "%s"' % (user, password))
        if access:
            out += self.command('setaccesslevel "%s" %s' % (user, access))
        return out

    # Lua ------------------------------------------------------------------------------------

    def lua(self, code, timeout_ms=10000):
        """Runs a Lua chunk in the server's Lua state (harness bridge) and returns its value;
        raises LuaError. Single expressions need `return`."""
        return self.bridge.lua(code, timeout_ms)

    def wait_until(self, lua_expr, timeout_s=10, poll_s=0.1):
        """Polls a server Lua expression until it is Lua-truthy; returns its value."""
        code = lua_expr if re.search(r"\breturn\b", lua_expr) else "return " + lua_expr
        deadline = time.time() + timeout_s
        while True:
            value = self.lua(code)
            if value is not None and value is not False:
                return value
            if time.time() >= deadline:
                raise TimeoutError("server wait_until(%r): still %r after %ss" % (lua_expr, value, timeout_s))
            time.sleep(poll_s)

    def players(self):
        """PZLab.players() on the server: every connected player {user, x, y, z, handle}."""
        return self.lua("return PZLab.players()") or []

    def clear_fixtures(self):
        """PZLab.clearFixtures() on the server (removals are sent to the clients)."""
        return self.lua("return PZLab.clearFixtures()")


__all__ = ["Server", "LuaError", "READY_LINE"]
