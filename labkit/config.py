"""Lab configuration, layered (later wins):
  built-in DEFAULTS -> lab.json (portable, committed) -> lab.local.json (this machine,
  gitignored, written by `pzlab init`) -> env PZLAB_GAME_DIR / PZLAB_PROFILE / PZLAB_SERVER_PROFILE.
Nothing here may name a specific machine, user or mod."""

import copy
import json
import os
from pathlib import Path

from . import detect

LAB_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "game_dir": None,          # auto-detected (Steam libraries) when unset
    "profile_dir": "profile",  # the game's -cachedir, relative to the repo root
    "options_overrides": {},
    "resolution": [1280, 720],
    "heap": None,              # None: use the game's own -Xmx from ProjectZomboid64.json
    "game_args": ["-debug", "-nosteam", "-nosound", "-novoip"],
    "mods": {},                # folder name -> path of a B42 mod folder (contains common/ and/or 42/)
    "enabled": [],             # folder names / mod IDs active by default (the harness always is)
    # Riverside (vanilla map): a house whose kitchen and bedroom hold many containers.
    "world": {"map": "Muldraugh, KY", "x": 6119, "y": 5474, "z": 0,
              "sandbox": {"Zombies": 6}, "god": True, "closeUI": ["UIDebugConsole", "ISCharacterInfoWindow"]},
    "keep_saves": 3,
    "window": {"x": None, "y": None, "background": False},
    # Lab server (multiplayer): a dedicated server (zombie.network.GameServer) on loopback with its
    # own cachedir. It runs lab.json's "world" (map, sandbox; x/y/z = where new characters spawn)
    # with the same mods as the client. See AGENT.md "Multiplayer".
    "server": {
        "profile_dir": None,        # None: "<profile_dir>-server" (profile-server, profile-agent-server, ...)
        "name": "pzlab",            # -servername: Server/<name>.ini, Saves/Multiplayer/<name>, db/<name>.db
        "ip": "127.0.0.1",          # -ip: the address the server binds; loopback keeps it off the network
        "port": 16361,              # DefaultPort (UDP); vanilla uses 16261, kept free for a person's own server
        "udp_port": 16362,          # UDPPort
        "admin_user": "admin",      # created/updated at every start (-adminusername/-adminpassword)
        "admin_password": "pzlab",
        "heap": None,               # None: the server launcher's own -Xmx
        "debug": False,             # True: -Ddebug (the server's debug mode)
        "fresh": True,              # every start archives the previous world and user db, keep_saves kept
        "options": {},              # Server/<name>.ini values on top of the lab's (profile.LAB_SERVER_OPTIONS)
        "args": [],                 # extra server command-line arguments
    },
}

HARNESS_ID = "PZLabHarness"


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


class Config:
    def __init__(self, path=None, profile_dir=None, server_profile_dir=None):
        """profile_dir / server_profile_dir override everything else (e.g. a second client)."""
        data = copy.deepcopy(DEFAULTS)
        files = [Path(path)] if path else [LAB_ROOT / "lab.json", LAB_ROOT / "lab.local.json"]
        self.sources = []
        for f in files:
            if f.exists():
                _merge(data, json.loads(f.read_text(encoding="utf-8")))
                self.sources.append(str(f))
        if os.environ.get("PZLAB_GAME_DIR"):
            data["game_dir"] = os.environ["PZLAB_GAME_DIR"]
        if os.environ.get("PZLAB_PROFILE"):
            data["profile_dir"] = os.environ["PZLAB_PROFILE"]
        if os.environ.get("PZLAB_SERVER_PROFILE"):
            data["server"]["profile_dir"] = os.environ["PZLAB_SERVER_PROFILE"]
        if profile_dir:
            data["profile_dir"] = str(profile_dir)
        if server_profile_dir:
            data["server"]["profile_dir"] = str(server_profile_dir)
        self.data = data
        self.profile_dir = self._resolve(data["profile_dir"])
        server_dir = data["server"].get("profile_dir") or (Path(data["profile_dir"]).name + "-server")
        self.server_profile_dir = self._resolve(server_dir)
        self.mods = {name: self._resolve(p) for name, p in data["mods"].items()}
        self.mods[HARNESS_ID] = LAB_ROOT / "harness" / HARNESS_ID
        self._game_dir = Path(data["game_dir"]) if data["game_dir"] else None

    @staticmethod
    def _resolve(p):
        p = Path(p)
        return (p if p.is_absolute() else LAB_ROOT / p).resolve()

    def __getitem__(self, key):
        return self.data[key]

    @property
    def game_dir(self):
        if self._game_dir is None:
            self._game_dir = detect.find_game_dir()
            if self._game_dir is None:
                raise FileNotFoundError(
                    "Project Zomboid not found. Run `pzlab.py init --game-dir <install dir>` "
                    "or set PZLAB_GAME_DIR.")
        return self._game_dir

    def add_mod_dir(self, path):
        """Registers a mod folder given on the command line; returns its folder name."""
        root = detect.mod_root(Path(path).resolve())
        self.mods[root.name] = root
        return root.name

    # Profile layout (the game's -cachedir) -------------------------------------------------

    @property
    def lua_dir(self):
        return self.profile_dir / "Lua" / "pzlab"

    @property
    def lua_scratch_dir(self):
        """Lua files the lab runs through the engine's file loader (Lab.load_lua)."""
        return self.profile_dir / "lua-scratch"

    @property
    def console_log(self):
        return self.profile_dir / "console.txt"

    @property
    def screenshot_dir(self):
        return self.profile_dir / "Screenshots"

    @property
    def shots_dir(self):
        return self.profile_dir / "shots"

    @property
    def pid_file(self):
        return self.profile_dir / "pzlab.pid"

    # Lab server profile (the dedicated server's -cachedir) ---------------------------------

    @property
    def server_lua_dir(self):
        return self.server_profile_dir / "Lua" / "pzlab"

    @property
    def server_pid_file(self):
        return self.server_profile_dir / "pzlab.pid"
