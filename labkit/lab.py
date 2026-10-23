"""Lab facade: set up, start, drive, inspect and stop a Project Zomboid instance."""

import contextlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import detect, imaging, locations, process, profile, win32
from .bridge import Bridge, LuaError
from .config import LAB_ROOT, Config
from .inputs import Input
from .logs import Log
from .server import Server


class NotPlayable(RuntimeError):
    """The game is not in a state where world input behaves normally (see Lab.require_playable)."""

    def __init__(self, problems, state):
        super().__init__("UI not playable: %s (escape would: %s)" % (", ".join(problems), state.get("escape")))
        self.problems = problems
        self.state = state
        self.result = state


def _ui_lists(state):
    """An empty Lua table arrives as {}; make the list-valued uiState fields lists again."""
    if isinstance(state, dict):
        for key in ("modals", "problems"):
            if state.get(key) == {}:
                state[key] = []
    return state


class Lab:
    def __init__(self, config_path=None, profile=None, server_profile=None):
        """profile / server_profile: the client's and the lab server's profile folders (relative to
        the repository), overriding lab.json and the PZLAB_PROFILE / PZLAB_SERVER_PROFILE env."""
        self.config_path = config_path
        self.cfg = Config(config_path, profile, server_profile)
        self.bridge = Bridge(self.cfg.lua_dir)
        self.log = Log(self.cfg)
        self.server = Server(self.cfg)  # the lab server (multiplayer); see labkit/server.py
        self._input = None
        self.last_hover = None

    # Lifecycle ------------------------------------------------------------------------------

    def init(self, game_dir=None, import_personal=True):
        """Writes lab.local.json for this machine: the game dir (given or detected) and,
        if this machine's own Zomboid profile has them, the person's Terms of Service acceptance
        and language. The lab never accepts the Terms of Service itself."""
        local_path = LAB_ROOT / "lab.local.json"
        local = json.loads(local_path.read_text(encoding="utf-8")) if local_path.exists() else {}
        game = Path(game_dir).resolve() if game_dir else detect.find_game_dir()
        if game is None or not (game / detect.JAR).exists():
            raise FileNotFoundError("Project Zomboid not found%s; pass --game-dir"
                                    % (" at %s" % game if game else ""))
        local["game_dir"] = str(game)
        personal = profile.personal_options_from_user_profile() if import_personal else {}
        local.setdefault("options_overrides", {}).update(personal)
        local_path.write_text(json.dumps(local, indent=2) + "\n", encoding="utf-8")
        self.cfg = Config()
        self.bridge = Bridge(self.cfg.lua_dir)
        self.log = Log(self.cfg)
        self.server = Server(self.cfg)
        result = {"wrote": str(local_path), "game_dir": str(game), "imported": personal}
        if "termsOfServiceVersion" not in personal:
            result["action_needed"] = ("No Terms of Service acceptance found on this machine. Run "
                                       "`pzlab.py start --menu` once and accept the terms in the game "
                                       "window yourself; the lab profile remembers it.")
        return result

    def doctor(self):
        """Environment report for running the lab on this machine."""
        import platform
        import sys
        report = {"python": sys.version.split()[0], "os": platform.platform(),
                  "pillow": imaging.have_pil(), "config_files": self.cfg.sources}
        try:
            game = self.cfg.game_dir
            report["game_dir"] = str(game)
            report["java"] = str(process.java_exe(game))
            report["mods_reset_marker"] = detect.mods_reset_marker(game)
            report["launch"] = process.command(self.cfg)
            report["server_launch"] = process.server_command(self.cfg)
            report["server_launch_source"] = detect.server_launch_spec(game)["source"]
        except (FileNotFoundError, ValueError) as e:
            report["problem"] = str(e)
        options = self.cfg.profile_dir / "options.ini"
        tos = self.cfg["options_overrides"].get("termsOfServiceVersion")
        if not tos and options.exists():
            tos = next((l.split("=", 1)[1] for l in options.read_text(errors="replace").splitlines()
                        if l.startswith("termsOfServiceVersion=")), None)
        report["terms_of_service_accepted"] = bool(tos)
        if platform.system() != "Windows":
            report["warning"] = "input injection and OS capture are Windows-only"
        return report

    def setup(self, enable=None, mod_dirs=None):
        cfg = self.cfg
        names = list(cfg["enabled"] if enable is None else enable)
        for path in mod_dirs or []:
            names.append(cfg.add_mod_dir(path))
        profile.ensure_dirs(cfg)
        profile.write_options(cfg)
        profile.link_mods(cfg)
        profile.write_locations(cfg)
        ids = profile.write_active_mods(cfg, [profile.mod_id(cfg, n) if n in cfg.mods else n for n in names])
        return {"profile": str(cfg.profile_dir), "game_dir": str(cfg.game_dir), "active_mods": ids}

    def client(self, profile):
        """Another lab client, with its own profile folder (e.g. "profile-2"), for this lab's
        server: multiplayer tests with several players. Each client is a separate game process
        and window; join each with its own account (lab.server.ensure_user, then
        other.start(join={"user": ..., "password": ...}))."""
        return Lab(self.config_path, profile=profile, server_profile=self.cfg.server_profile_dir)

    def start(self, enable=None, world=None, autostart=True, wait=True, timeout_s=300, mod_dirs=None,
              join=False):
        """Sets up the profile and launches the game: into a fresh single-player world (autostart),
        or to the main menu (autostart=False). join=True (or a dict of Lab.join arguments, e.g.
        {"user": "player2", "password": "pw"}): multiplayer instead: starts the lab server first
        if it isn't running (same mods and world), launches to the main menu and joins it
        (Lab.join); result["join"] has the join report."""
        cfg = self.cfg
        if join:
            server = None
            if not self.server.pid():
                server = self.server.start(enable, mod_dirs, world=world, timeout_s=timeout_s)
            result = self.start(enable, world, autostart=False, wait=True, timeout_s=timeout_s, mod_dirs=mod_dirs)
            if server:
                result["server"] = {k: server.get(k) for k in ("pid", "address", "active_mods", "archived", "ready_s")}
            result["join"] = self.join(timeout_s=timeout_s, **(join if isinstance(join, dict) else {}))
            return result
        info = self.setup(enable, mod_dirs)
        profile.clear_runtime_files(cfg)
        profile.clear_lua_scratch(cfg)
        pruned = profile.prune_saves(cfg, cfg["keep_saves"])
        if autostart:
            profile.write_autostart(cfg, dict(cfg["world"], **(world or {})))
        # The previous run's log would satisfy wait_state's "loading finished" check early.
        try:
            cfg.console_log.unlink()
        except FileNotFoundError:
            pass
        pid = process.launch(cfg)
        self._input = None
        result = dict(info, pid=pid, pruned_saves=pruned)
        if wait:
            # console.txt is recreated on launch, so offset 0 is this run's log.
            result["state"] = self.wait_state("ingame" if autostart else "menu", timeout_s,
                                              log_offset=0 if autostart else None)
            self.place_window()
        return result

    def launch_world(self, world=None, wait=True, timeout_s=300):
        """From the main menu of a running lab game, starts a fresh world (lab.json "world")."""
        spec = dict(self.cfg["world"], **(world or {}))
        log_offset = self.log.size()
        self.lua("PZLab.launchWorld(%s) return true" % profile.to_lua(spec))
        if not wait:
            return {"launched": spec}
        return {"launched": spec, "state": self.wait_state("ingame", timeout_s, log_offset=log_offset)}

    def wait_state(self, target, timeout_s=300, settle_s=2.0, log_offset=None, check=None):
        """Waits until the harness heartbeat reports `target` ("menu" or "ingame").

        Loading a world ends on a "Click to Start" screen where no Lua runs; when waiting for
        "ingame" with a log_offset, a click is injected once the log says loading finished.
        check(state) is called with every heartbeat read (may be None) and may raise to stop."""
        deadline = time.time() + timeout_s
        next_click = 0
        while time.time() < deadline:
            if not process.running_pid(self.cfg):
                raise RuntimeError("game exited while waiting for '%s'. Errors:\n%s"
                                   % (target, "\n".join(self.log.errors(since=0)[-40:])))
            state = self.bridge.state()
            if check:
                check(state)
            if state and state["age_s"] < 3 and state["state"] == target:
                time.sleep(settle_s)
                return self.bridge.state() or state
            if (target == "ingame" and log_offset is not None and time.time() >= next_click
                    and any("game loading took" in line for line in self.log.lines(since=log_offset))):
                next_click = time.time() + 2
                try:
                    width, height = win32.client_size(self.hwnd)
                    self.input.click(width // 2, height // 2)
                except RuntimeError:
                    pass  # window not up yet
            time.sleep(0.5)
        raise TimeoutError(self._stall_report(target, timeout_s))

    def _stall_report(self, target, timeout_s):
        lines = self.log.lines(since=0)
        states = [l.split("STATE:", 1)[1].strip() for l in lines if "STATE:" in l]
        report = ["harness did not reach '%s' within %ds" % (target, timeout_s),
                  "last heartbeat: %s" % self.bridge.state(),
                  "game states seen: %s" % (", ".join(states[-6:]) or "none")]
        if not any("loading PZLabHarness" in l for l in lines):
            report.append("PZLabHarness never loaded: mods/default.txt was probably reset at boot "
                          "(reset marker name changed in this game version?)")
        try:
            report.append("window capture: %s" % self.os_screenshot())
        except Exception as e:  # diagnostics must not mask the timeout
            report.append("window capture failed: %s" % e)
        options = self.cfg.profile_dir / "options.ini"
        accepted = options.exists() and any(
            l.startswith("termsOfServiceVersion=") and l.split("=", 1)[1].strip()
            for l in options.read_text(errors="replace").splitlines())
        if not accepted:
            report.append("TERMS OF SERVICE not accepted in this lab profile: the game is waiting on it. "
                          "A person must click ACCEPT in the game window (it may be behind other windows), "
                          "or run `pzlab.py init` if they already accepted in their own Zomboid profile.")
        return "\n".join(report)

    def join(self, user=None, password=None, server_password="", ip=None, port=None, timeout_s=300,
             close_chat=True, clear_weather=True):
        """Joins a multiplayer server from the main menu of the running lab client (default: the
        lab server, as its admin account) and waits until the player is in the world. Drives the
        vanilla Direct Connect popup and, on a first join, character creation (PZLab.joinServer,
        client/PZLab_Join.lua), then dismisses "Click to Start". A client started with -debug
        (the lab default) is refused by the server unless the account may ConnectWithDebug
        (admin; lab.server.ensure_user creates others). Raises RuntimeError with vanilla's
        reason and the join events when the connection fails.
        Once in the world: lab.json world.closeUI is applied; close_chat also takes the chat window
        (ISChat) out of the UI, which covers the left of the screen and so blocks world picking
        there; clear_weather runs PZLab.clearWeather (admin fog/precipitation overrides; the
        server's random weather often starts in dense fog).
        Returns {user, ip, port, created (character creation was driven), pressed, events, ms, state}."""
        server = self.cfg["server"]
        cfg = {"ip": ip or server["ip"], "port": int(port or server["port"]),
               "user": user or server["admin_user"],
               "password": server["admin_password"] if password is None and not user else (password or ""),
               "serverPassword": server_password or ""}
        state = self.bridge.state()
        if state and state.get("state") == "ingame":
            raise RuntimeError("the lab client is already in a game; stop it or quit to the main menu first")
        if not state or state.get("state") != "menu" or state.get("age_s", 99) > 3:
            self.wait_state("menu", timeout_s=min(timeout_s, 120))
        started = time.time()
        offset = self.log.size()
        attempt = self.lua("return PZLab.joinServer(%s).started" % profile.to_lua(cfg))
        # The client resets its Lua state after connecting (PZLab_Join.lua resumes from join.txt),
        # so events and pressed screens are collected across heartbeats; a heartbeat written before
        # this attempt (an earlier one's state) is skipped.
        events, pressed = [], []

        def check(state):
            connect = (state or {}).get("connect")
            if not connect or connect.get("started") != attempt:
                return
            for e in connect.get("events") or []:
                if e not in events:
                    events.append(e)
            for screen in connect.get("pressed") or []:
                if screen not in pressed:
                    pressed.append(screen)
            if connect.get("phase") == "failed":
                raise RuntimeError("joining %s:%s as %s failed: %s\nevents:\n  %s" % (
                    cfg["ip"], cfg["port"], cfg["user"], connect.get("message"), "\n  ".join(events)))

        final = self.wait_state("ingame", timeout_s, log_offset=offset, check=check)
        closing = list(self.cfg["world"].get("closeUI") or []) + (["ISChat"] if close_chat else [])
        if closing:
            self.lua("PZLab.pendingCloseUI = %s return true" % profile.to_lua(closing))
        if clear_weather:
            self.lua("return PZLab.clearWeather()")
        try:
            self.place_window()
        except RuntimeError:
            pass
        return {"user": cfg["user"], "ip": cfg["ip"], "port": cfg["port"],
                "created": "CharacterCreationMain" in pressed, "pressed": pressed,
                "events": events, "ms": int((time.time() - started) * 1000), "state": final}

    def stop(self, timeout_s=20, force=False):
        cfg = self.cfg
        pid = process.running_pid(cfg)
        if not pid:
            return {"stopped": False, "reason": "not running"}
        how = "killed"
        if not force and self.bridge.alive():
            self.send("getCore():quitToDesktop()")
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
        pid = process.running_pid(self.cfg)
        hwnd = process.game_window(pid) if pid else None
        return {
            "running": bool(pid),
            "pid": pid,
            "window": {"hwnd": hwnd, "client": win32.client_size(hwnd), "rect": win32.window_rect(hwnd)} if hwnd else None,
            "harness": self.bridge.state(),
        }

    # Lua ------------------------------------------------------------------------------------

    def lua(self, code, timeout_ms=10000):
        return self.bridge.lua(code, timeout_ms)

    def send(self, code):
        """Fire-and-forget command (no reply expected, e.g. quitting)."""
        return self.bridge.send(code)

    def reload(self, source_file):
        """Hot-reloads a mod Lua file given its source path, resolved through the profile's mod links."""
        source_file = Path(source_file).resolve()
        for link in (self.cfg.profile_dir / "mods").iterdir():
            if not link.is_dir():
                continue
            root = Path(os.path.realpath(link))
            if root in source_file.parents:
                game_path = link / source_file.relative_to(root)
                return self.lua("return PZLab.reload(%s)" % profile.to_lua(str(game_path)))
        raise ValueError("%s is not inside any mod linked into the lab profile" % source_file)

    # Lua files through the engine's file loader --------------------------------------------

    LUA_AFTER_FILE = "_pzlab_bridge.lua"  # reserved: run after each load (see load_lua)

    def lua_file_path(self, name):
        """Where load_lua keeps the file `name` (".lua" appended if missing; subfolders with "/"):
        <profile>/lua-scratch/<name>. Raises ValueError for names the engine or the lab refuses."""
        name = name.replace("\\", "/")
        if not name.endswith(".lua"):
            name += ".lua"
        parts = name.split("/")
        # reloadLuaFile silently refuses any path containing ".." (LuaManager.hasRelativePath)
        if (".." in name or name.startswith("/") or any(p in ("", ".") for p in parts)
                or re.search(r'[<>:"|?*\x00-\x1f]', name) or parts[0].startswith("_pzlab")):
            raise ValueError("invalid Lua file name %r (relative, no '..', not starting with _pzlab)" % name)
        return self.cfg.lua_scratch_dir.joinpath(*parts)

    def load_lua(self, name, source, hot=None, timeout_ms=10000):
        """Runs Lua `source` as the file `name` through the engine's own file loader, from the
        lab's scratch folder (<profile>/lua-scratch/, under the cachedir, which reloadLuaFile
        accepts). The chunk gets real file semantics: its file name in prototypes and stack
        traces, and the engine's hot reload when loaded again under the same name.

        hot=None: the first load of `name` in this game session runs like the engine's boot-time
        load of a mod file (Events.X.Add registers), later loads are hot reloads; hot=True:
        always reloadLuaFile (Events.X.Add/Remove do nothing, new closures reroute registered
        callbacks: AGENT.md traps); hot=False: always boot-like. Source that doesn't compile
        raises LuaError and is not loaded. Runtime errors don't raise: they are in "errors"
        (log.exceptions logged during the load; also "error" for a boot-like load).
        Returns {name, path, hot, loads, value (the chunk's first return value), errors[, error]}."""
        path = self.lua_file_path(name)
        game_path = path.as_posix()
        info = self.lua("return PZLab.luaFileInfo(%s, %s)" % (profile.to_lua(game_path),
                                                              profile.lua_long_string(source)), timeout_ms)
        if info.get("compileError"):
            raise LuaError("%s does not compile: %s" % (name, info["compileError"]), info)
        hot = info.get("loads", 0) > 0 if hot is None else bool(hot)
        path.parent.mkdir(parents=True, exist_ok=True)
        after = self.cfg.lua_scratch_dir / self.LUA_AFTER_FILE
        if not after.exists():
            after.write_bytes(b"")
        data = source.encode("utf-8")
        # Boot-like: the file is empty while the engine runs it (it only sets the compiler's
        # current file), the source goes inline; then the file gets the source for later reloads.
        path.write_bytes(data if hot else b"")
        offset = self.log.size()
        try:
            result = self.lua("return PZLab.loadLua(%s, %s, %s, %s)" % (
                profile.to_lua(game_path), "nil" if hot else profile.lua_long_string(source),
                profile.to_lua(hot), profile.to_lua(after.as_posix())), timeout_ms)
        finally:
            if not hot:
                path.write_bytes(data)
        result["name"] = path.relative_to(self.cfg.lua_scratch_dir).as_posix()
        result.setdefault("value", None)  # a nil field is absent from the JSON reply
        result["errors"] = self.log.exceptions(since=offset)
        return result

    def clear_lua_files(self):
        """Deletes every file load_lua wrote (also done by start); returns how many. Code the
        game already loaded from them stays loaded."""
        return profile.clear_lua_scratch(self.cfg)

    def check(self, paths):
        """Syntax-checks Lua files/dirs with the game's own Kahlua compiler (no game launch, no JDK)."""
        import subprocess
        checker = Path(__file__).resolve().parent / "luacheck"
        cp = os.pathsep.join([str(checker), str(self.cfg.game_dir / detect.JAR)])
        run = subprocess.run([str(process.java_exe(self.cfg.game_dir)), "-cp", cp, "LuaCheck", *map(str, paths)],
                             capture_output=True, text=True)
        return {"ok": run.returncode == 0, "output": (run.stdout + run.stderr).strip().splitlines()}

    # Window and input -----------------------------------------------------------------------

    @property
    def hwnd(self):
        pid = process.running_pid(self.cfg)
        if not pid:
            raise RuntimeError("lab game is not running")
        hwnd = process.game_window(pid)
        if not hwnd:
            raise RuntimeError("game window not found (still starting?)")
        return hwnd

    @property
    def input(self):
        hwnd = self.hwnd
        if self._input is None or self._input.hwnd != hwnd:
            self._input = Input(hwnd)
        return self._input

    def hover(self, obj_expr, max_points=12, settle_s=0.12, allow_ui=False):
        """Moves the mouse onto a world object so the engine itself picks it.

        obj_expr is a Lua expression evaluating to the object (e.g.
        'PZLab.square(6119,5472,0):getObjects():get(2)' or 'PZLab.get(3)') or a handle id.
        Works for tile objects, floor items, corpses and vehicles: tries PZLab.aimPoints until
        PZLab.isHovered confirms the engine's own picker resolves the mouse to the object.
        Candidates under visible UI are skipped (PZLab.pickBlockedBy): the engine picks no world
        object while the cursor is over any visible top-level element, including click-through
        ones that let clicks reach the world (clickThrough: True); allow_ui=True tries them
        anyway (only picker checks, corpses and vehicles, can pass). Returns the point {x, y}
        (plus "covered": the skipped points, when there were any), or None. The full report is
        kept in self.last_hover: {point, candidates, tried, covered: [{x, y, ui, clickThrough}]}."""
        if isinstance(obj_expr, int):
            obj_expr = "PZLab.get(%d)" % obj_expr
        points = self.lua("PZLab.target = %s return PZLab.aimPoints(PZLab.target, %d)" % (obj_expr, max_points))
        points = points or []
        covered = [] if allow_ui else [{"x": round(p["x"]), "y": round(p["y"]), "ui": p["ui"],
                                        "clickThrough": bool(p.get("clickThrough"))} for p in points if p.get("ui")]
        report = {"point": None, "candidates": len(points), "tried": 0, "covered": covered}
        self.last_hover = report
        check = "return PZLab.isHovered(PZLab.target, nil, nil, %s)" % ("true" if allow_ui else "false")
        for p in points:
            if p.get("ui") and not allow_ui:
                continue
            report["tried"] += 1
            self.input.move(p["x"], p["y"])
            time.sleep(settle_s)
            if self.lua(check):
                point = {"x": round(p["x"]), "y": round(p["y"])}
                report["point"] = dict(point)
                if report["covered"]:
                    point["covered"] = report["covered"]
                return point
        return None

    # Waiting ----------------------------------------------------------------------------------

    def wait_until(self, lua_expr, timeout_s=10, poll_s=0.1):
        """Polls a Lua expression (or a chunk containing `return`) until it is Lua-truthy
        (anything but nil/false; 0 and "" count as true). Returns its value; raises TimeoutError
        with the last value. Example: lab.wait_until("not PZLab.playerBusy()")."""
        code = lua_expr if re.search(r"\breturn\b", lua_expr) else "return " + lua_expr
        deadline = time.time() + timeout_s
        while True:
            value = self.lua(code)
            if value is not None and value is not False:
                return value
            if time.time() >= deadline:
                raise TimeoutError("wait_until(%r): still %r after %ss" % (lua_expr, value, timeout_s))
            time.sleep(poll_s)

    # UI state -------------------------------------------------------------------------------

    def ui_state(self):
        """PZLab.uiState(): pauseMenu, contextMenus (count), modals, speed, paused, mouse,
        mouseOverUI (what a click at the mouse would hit), pickBlockedBy (the element that keeps
        the world from being picked at the mouse, click-through ones included), focus (text
        entry), drag, escape (what Escape would do now), problems (empty when world input
        behaves normally) and playable."""
        return _ui_lists(self.lua("return PZLab.uiState()"))

    def ensure_playable(self, speed=1):
        """PZLab.ensurePlayable: leaves the pause menu the way its Resume item does, closes context
        menus, takes keyboard focus off a text entry, sets game speed (0-4). Never answers modal
        dialogs; they stay in after["problems"].
        Returns {before, after, closed: {pauseMenu, contextMenus, textEntry}[, errors]}."""
        result = self.lua("return PZLab.ensurePlayable({ speed = %d })" % int(speed))
        _ui_lists(result.get("before"))
        _ui_lists(result.get("after"))
        return result

    def close_context_menus(self):
        """PZLab.closeContextMenus(): closes visible context menus with vanilla's own calls; returns the count."""
        return self.lua("return PZLab.closeContextMenus()")

    def close_pause_menu(self):
        """PZLab.closePauseMenu(): True if it left the in-game pause menu, False if none was open."""
        return self.lua("return PZLab.closePauseMenu()")

    def context_menus(self):
        """PZLab.contextMenus(): visible context menus with their options and click points."""
        return self.lua("return PZLab.contextMenus()") or []

    def require_playable(self, allow=()):
        """Raises NotPlayable unless uiState reports no problems other than those in `allow`
        (exact strings such as "contextMenus", or prefixes such as "modal"). Returns the state.
        Call before acting so a stray pause menu or dialog can't silently void a test."""
        state = self.ui_state()
        bad = [p for p in state.get("problems", []) if p not in allow and p.split(":")[0] not in allow]
        if bad:
            raise NotPlayable(bad, state)
        return state

    # Inventory panes ------------------------------------------------------------------------

    def inventory_panes(self):
        """PZLab.inventoryPanes(): every visible ISInventoryPane (handle, rects, container, owner)."""
        return self.lua("return PZLab.inventoryPanes()") or []  # an empty Lua table encodes as {}

    def pane_rows(self, pane):
        """PZLab.paneRows(pane): the on-screen rows of an inventory pane (handle id)."""
        return self.lua("return PZLab.paneRows(%d)" % pane) or []

    def _poll(self, lua_expr, timeout_s, poll_s=0.03):
        """True once a Lua expression is truthy, False after timeout_s (never raises on timeout)."""
        deadline = time.time() + timeout_s
        while True:
            value = self.lua("return " + lua_expr)
            if value is not None and value is not False:
                return True
            if time.time() >= deadline:
                return False
            time.sleep(poll_s)

    def _covered(self, pane, x, y):
        """PZLab.coveredBy: what takes a click at (x, y) instead of the pane's window, or None."""
        return self.lua("return PZLab.coveredBy(%d, %r, %r)" % (pane, x, y)) or None

    @staticmethod
    def _describe_ui(d):
        """'ISInventoryPage "Loot" (handle 12)' for a PZLab.uiAt description."""
        if d.get("type") == "world":
            return "no window (the click would reach the world)"
        text = d.get("type") or "?"
        if d.get("title") or d.get("name"):
            text += ' "%s"' % (d.get("title") or d.get("name"))
        if d.get("handle") is not None:
            text += " (handle %s)" % d["handle"]
        child = d.get("child")
        if child:
            text += ", its child %s (handle %s)" % (child.get("type") or "?", child.get("handle"))
        return text + (", capturing the mouse" if d.get("capture") else "")

    def drag_row(self, src_pane, row_index, dst_pane, modifiers=(), steps=10, settle_s=0.15, phase_timeout_s=2):
        """Drags row `row_index` (PZLab.paneRows index) of inventory pane `src_pane` onto pane
        `dst_pane` (handles) with real mouse input, holding `modifiers` throughout. First checks
        that nothing covers the row or the drop point (PZLab.coveredBy): a window in front would
        take the press or the drop, so it raises naming that window instead of dragging. Each
        phase waits for the game to acknowledge it, so slow frames can't reorder press and
        moves: the row is under the mouse (src.mouseOverOption), the press started vanilla's
        item drag (ISMouseDrag.draggingFocus == src), the destination sees the mouse
        (isMouseOver), and the drop was handled (ISMouseDrag.dragging cleared). Returns what it
        did, including the player's action queue right after the drop; wait for the transfer
        with lab.wait_until("not PZLab.playerBusy()")."""
        info = self.lua("""
            local rows, row = PZLab.paneRows(%d), nil
            for _, r in ipairs(rows) do if r.index == %d then row = r end end
            local visible = {}
            for i, r in ipairs(rows) do visible[i] = r.index end
            local dst = PZLab.paneInfo(%d)
            return { row = row, visible = visible, dst = dst,
                     srcCover = row and PZLab.coveredBy(%d, row.point.x, row.point.y) or false,
                     dstCover = PZLab.coveredBy(%d, dst.drop.x, dst.drop.y) }
        """ % (src_pane, row_index, dst_pane, src_pane, dst_pane))
        row, dst = info.get("row"), info["dst"]
        if not row:
            raise ValueError("row %d of pane %d is not on screen (visible rows: %s)"
                             % (row_index, src_pane, info.get("visible")))
        if not dst.get("shown"):
            raise ValueError("destination pane %d shows no list area (window collapsed or hidden?); "
                             "for vanilla windows call PZLab.openInventoryWindows() first" % dst_pane)
        (x1, y1), (x2, y2) = (row["point"]["x"], row["point"]["y"]), (dst["drop"]["x"], dst["drop"]["y"])
        for what, pane, point, cover in (("row %d of pane %d" % (row_index, src_pane), src_pane, (x1, y1),
                                          info.get("srcCover")),
                                         ("the drop point of pane %d" % dst_pane, dst_pane, (x2, y2),
                                          info.get("dstCover"))):
            if cover:
                raise RuntimeError("%s is covered at %s by %s: a click there goes to it, not to the pane's "
                                   "window; move or close it first" % (what, point, self._describe_ui(cover)))

        def covered_note(pane, x, y):
            cover = self._covered(pane, x, y)
            return "; the point is now covered by %s" % self._describe_ui(cover) if cover else ""

        inp = self.input
        src_el, dst_el = "PZLab.uiElement(%d)" % src_pane, "PZLab.uiElement(%d)" % dst_pane
        held = inp.press_modifiers(modifiers)
        pressed = False
        try:
            inp.move(x1, y1)
            time.sleep(settle_s)
            if not self._poll("%s.mouseOverOption == %d" % (src_el, row_index), phase_timeout_s):
                raise RuntimeError("mouse at %s but pane %d has mouseOverOption %r, not row %d%s" % (
                    (x1, y1), src_pane, self.lua("return %s.mouseOverOption" % src_el), row_index,
                    covered_note(src_pane, x1, y1)))
            inp.down("left")
            pressed = True
            if not self._poll("ISMouseDrag.dragging ~= nil and ISMouseDrag.draggingFocus == %s" % src_el,
                              phase_timeout_s):
                raise RuntimeError("pressing row %d of pane %d did not start an item drag%s"
                                   % (row_index, src_pane, covered_note(src_pane, x1, y1)))
            for i in range(1, steps + 1):
                inp.move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
                time.sleep(inp.frame_s)
            if not self._poll("%s:isMouseOver()" % dst_el, phase_timeout_s):
                raise RuntimeError("destination pane %d does not see the mouse at %s%s"
                                   % (dst_pane, (x2, y2), covered_note(dst_pane, x2, y2)))
            time.sleep(settle_s)
            inp.up("left")
            pressed = False
            if not self._poll("ISMouseDrag.dragging == nil", phase_timeout_s):
                raise RuntimeError("the drop on pane %d was not handled (ISMouseDrag.dragging still set)%s"
                                   % (dst_pane, covered_note(dst_pane, x2, y2)))
        finally:
            if pressed:
                inp.up("left")
            inp.release_modifiers(held)
        return {"src": src_pane, "dst": dst_pane, "row": row, "from": [x1, y1], "to": [x2, y2],
                "modifiers": list(held), "actions": self.lua("return PZLab.actionQueue()")}

    def show_inventory_panes(self, player=0, pin=True, inventory=None, loot=None, timeout_s=3):
        """PZLab.showInventoryPanes: makes the player's vanilla inventory and loot windows visible,
        expanded and (pin=True) pinned, inside the screen and in front; inventory / loot =
        {"x", "y", "w", "h"} also places them. Waits until both panes show their list.
        Returns {"inventory": {window, pane, rect, info}, "loot": {...}}; info = PZLab.paneInfo."""
        opts = {"player": player, "pin": pin}
        if inventory:
            opts["inventory"] = inventory
        if loot:
            opts["loot"] = loot
        self.lua("return PZLab.showInventoryPanes(%s)" % profile.to_lua(opts))
        deadline = time.time() + timeout_s
        while True:
            # a frame later: vanilla's update() has run and the pane geometry is final
            result = self.lua("return PZLab.inventoryPanesOf(%d)" % player)
            if all(result.get(k, {}).get("info", {}).get("shown") for k in ("inventory", "loot")):
                return result
            if time.time() >= deadline:
                raise RuntimeError("inventory panes not shown after %ss: %s" % (timeout_s, result))
            time.sleep(0.05)

    # UI layout ------------------------------------------------------------------------------

    def save_layout(self):
        """PZLab.saveLayout(): snapshot of every top-level UI element (position, size, visible,
        attached, pinned, collapsed). Returns it ({"id", "windows": [...]})."""
        return self.lua("return PZLab.saveLayout()")

    def restore_layout(self, snapshot, hide_added=False):
        """PZLab.restoreLayout: puts every element of a snapshot (dict or id) back as it was.
        hide_added=True also hides top-level elements that appeared since. Returns
        {restored, changed: [{type, title, fields}], added: [...], missing: [...]}."""
        snap_id = snapshot["id"] if isinstance(snapshot, dict) else int(snapshot)
        result = self.lua("return PZLab.restoreLayout(%d, { hideAdded = %s })"
                          % (snap_id, "true" if hide_added else "false"))
        for key in ("changed", "added", "missing"):
            if result.get(key) == {}:
                result[key] = []
        return result

    @contextlib.contextmanager
    def ui_layout(self, hide_added=False):
        """Context manager: snapshot the UI layout, run the block, restore it (also on errors).
        with lab.ui_layout(): lab.show_inventory_panes(); ...  The snapshot is yielded."""
        snapshot = self.save_layout()
        try:
            yield snapshot
        finally:
            self.restore_layout(snapshot, hide_added=hide_added)

    # Camera and places ----------------------------------------------------------------------

    def get_zoom(self):
        """PZLab.getZoom(): the camera zoom (Core.getZoom(0); > 1 shows more world)."""
        return self.lua("return PZLab.getZoom()")

    def zoom_levels(self):
        """PZLab.zoomLevels(): supported zoom levels, closest first."""
        return self.lua("return PZLab.zoomLevels()")

    def set_zoom(self, zoom, timeout_s=5):
        """PZLab.setZoom: snaps to the nearest supported level, scrolls there like the mouse
        wheel and waits until the camera has settled. Returns the final zoom."""
        return self.lua("return PZLab.setZoom(%r, %d)" % (float(zoom), timeout_s * 1000),
                        timeout_ms=int(timeout_s * 1000) + 2000)

    def locations(self):
        """The test-location catalogue (labkit/locations.json): name -> entry."""
        return locations.load()

    def goto(self, target, radius=10, timeout_s=30, anywhere=False):
        """Teleports the player to a catalogue location (name) or (x, y[, z]) and waits until the
        area around it is loaded and the player stands there (PZLab.gotoLocation; whole numbers =
        the tile centre). Refuses a target off the map (PZLab.onMap) unless anywhere=True.
        Multiplayer: the server teleports the player (admin); when the client is on the lab
        server, also waits until the server has the player at the client's position
        (result["server"]). Returns {name, x, y, z, player, ms[, sent, server]}."""
        pos = locations.position(target)
        spec = {"x": pos["x"], "y": pos["y"], "z": pos["z"]}
        opts = {"radius": radius, "timeoutMs": int(timeout_s * 1000)}
        if anywhere:
            opts["anywhere"] = True
        result = self.lua("return PZLab.gotoLocation(%s, %s)" % (profile.to_lua(spec), profile.to_lua(opts)),
                          timeout_ms=int(timeout_s * 1000) + 10000)
        if isinstance(target, str):
            result["name"] = target
        if "sent" in result:  # multiplayer
            server = self._server_agrees(result["player"])
            if server:
                result["server"] = server
        return result

    def _server_agrees(self, client_pos, timeout_s=5.0, tolerance=0.3):
        """Multiplayer on the lab server: waits until the server's copy of this client's player is
        within `tolerance` tiles of client_pos (it follows the client's position packets) and
        returns it; None when the client isn't on the lab server. Raises TimeoutError otherwise."""
        if not self.server.pid():
            return None
        user = self.lua("return getOnlineUsername()")
        code = ("local p = PZLab.player(%s) if not p then return false end "
                "return { x = p:getX(), y = p:getY(), z = p:getZ() }" % profile.to_lua(user))
        deadline = time.time() + timeout_s
        while True:
            server = self.server.lua(code)
            if server is False or server is None:
                return None  # not a player of the lab server
            if (abs(server["x"] - client_pos["x"]) <= tolerance and abs(server["y"] - client_pos["y"]) <= tolerance
                    and int(server["z"]) == int(client_pos["z"])):
                return server
            if time.time() >= deadline:
                raise TimeoutError("after goto the lab server still has %s at %s, the client at %s"
                                   % (user, server, client_pos))
            time.sleep(0.1)

    # Fixtures -------------------------------------------------------------------------------

    def clear_fixtures(self):
        """PZLab.clearFixtures(): removes every tracked fixture still in the world; returns the count."""
        return self.lua("return PZLab.clearFixtures()")

    def place_window(self, x=None, y=None, background=None):
        opts = self.cfg["window"]
        x = opts.get("x") if x is None else x
        y = opts.get("y") if y is None else y
        background = opts.get("background") if background is None else background
        hwnd = self.hwnd
        if x is not None and y is not None:
            win32.set_window_pos(hwnd, win32.HWND_TOP, x, y, win32.SWP_NOSIZE | win32.SWP_NOACTIVATE)
        if background:
            win32.set_window_pos(hwnd, win32.HWND_BOTTOM, 0, 0,
                                 win32.SWP_NOMOVE | win32.SWP_NOSIZE | win32.SWP_NOACTIVATE)
        return win32.window_rect(hwnd)

    # Screenshots ----------------------------------------------------------------------------

    def screenshot(self, out=None, crop=None, scale=None, timeout_s=10, check=False):
        """Captures the game's own frame buffer (UI included). crop=(x, y, w, h) in UI pixels.
        Returns the path; with check=True returns {"path", "problems"}: uiState problems at the
        moment of the capture (same Lua call), e.g. ["pauseMenu", "paused"]."""
        # takeScreenshot(name) writes <profile>/Screenshots/<name> verbatim, on the render thread.
        name = "pzlab_%d.png" % time.time_ns()
        found = self.cfg.screenshot_dir / name
        started = time.time()
        problems = self.lua("PZLab.screenshot(%s) return %s" % (
            profile.to_lua(name), "PZLab.uiState().problems" if check else "true"))
        while True:
            if found.exists():
                size = found.stat().st_size
                time.sleep(0.2)
                if size > 0 and found.stat().st_size == size:
                    break
            if time.time() - started > timeout_s:
                raise TimeoutError("screenshot %s did not appear" % found)
            time.sleep(0.1)
        dest = self._shot_path(out)
        if crop or scale:
            imaging.save_png_file(found, dest, crop, scale)
            found.unlink()
        else:
            shutil.move(found, dest)
        if check:
            return {"path": str(dest), "problems": problems or []}
        return str(dest)

    def os_screenshot(self, out=None, crop=None, scale=None):
        """Captures the window through the OS (PrintWindow): no harness needed, works while
        covered. Use for menus, loading screens, or a hung/halted Lua state."""
        from .capture import capture_window
        dest = self._shot_path(out)
        width, height, buffer = capture_window(self.hwnd)
        imaging.save_bgrx(dest, width, height, buffer, crop, scale)
        return str(dest)

    def _shot_path(self, out):
        dest = Path(out) if out else self.cfg.shots_dir / ("shot_%s_%03d.png" % (
            time.strftime("%Y%m%d_%H%M%S"), int(time.time() * 1000) % 1000))
        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest
