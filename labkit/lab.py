"""Lab facade: set up, start, drive, inspect and stop a Project Zomboid instance."""

import json
import os
import shutil
import time
from pathlib import Path

from . import detect, imaging, process, profile, win32
from .bridge import Bridge
from .config import LAB_ROOT, Config
from .inputs import Input
from .logs import Log


class Lab:
    def __init__(self, config_path=None):
        self.cfg = Config(config_path)
        self.bridge = Bridge(self.cfg.lua_dir)
        self.log = Log(self.cfg)
        self._input = None

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
        ids = profile.write_active_mods(cfg, [profile.mod_id(cfg, n) if n in cfg.mods else n for n in names])
        return {"profile": str(cfg.profile_dir), "game_dir": str(cfg.game_dir), "active_mods": ids}

    def start(self, enable=None, world=None, autostart=True, wait=True, timeout_s=300, mod_dirs=None):
        cfg = self.cfg
        info = self.setup(enable, mod_dirs)
        profile.clear_runtime_files(cfg)
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

    def wait_state(self, target, timeout_s=300, settle_s=2.0, log_offset=None):
        """Waits until the harness heartbeat reports `target` ("menu" or "ingame").

        Loading a world ends on a "Click to Start" screen where no Lua runs; when waiting for
        "ingame" with a log_offset, a click is injected once the log says loading finished."""
        deadline = time.time() + timeout_s
        next_click = 0
        while time.time() < deadline:
            if not process.running_pid(self.cfg):
                raise RuntimeError("game exited while waiting for '%s'. Errors:\n%s"
                                   % (target, "\n".join(self.log.errors(since=0)[-40:])))
            state = self.bridge.state()
            if state and state["age_s"] < 3 and state["state"] == target:
                time.sleep(settle_s)
                return self.bridge.state()
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
        tmp = self.cfg.lua_dir / "cmd.tmp"
        tmp.write_text("PZLAB %d 60000\n%s" % (time.time_ns(), code), encoding="utf-8")
        os.replace(tmp, self.cfg.lua_dir / "cmd.txt")

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

    def hover(self, obj_expr, max_points=12, settle_s=0.12):
        """Moves the mouse onto a world object so the engine itself picks it.

        obj_expr is a Lua expression evaluating to the IsoObject (e.g.
        'PZLab.square(6119,5472,0):getObjects():get(2)'). Tries points on the sprite's opaque
        pixels until UIManager.getLastPicked() is that object. Returns the point, or None."""
        points = self.lua("PZLab.target = %s return PZLab.aimPoints(PZLab.target, %d)" % (obj_expr, max_points))
        for p in points or []:
            self.input.move(p["x"], p["y"])
            time.sleep(settle_s)
            if self.lua("return UIManager.getLastPicked() == PZLab.target"):
                return {"x": round(p["x"]), "y": round(p["y"])}
        return None

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

    def screenshot(self, out=None, crop=None, scale=None, timeout_s=10):
        """Captures the game's own frame buffer (UI included). crop=(x, y, w, h) in UI pixels."""
        # takeScreenshot(name) writes <profile>/Screenshots/<name> verbatim, on the render thread.
        name = "pzlab_%d.png" % time.time_ns()
        found = self.cfg.screenshot_dir / name
        started = time.time()
        self.lua("return PZLab.screenshot(%s)" % profile.to_lua(name))
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
