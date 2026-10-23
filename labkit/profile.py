"""Prepares the lab profile (the game's -cachedir): options, mod links, active mods,
autostart request. Everything written here stays inside the profile directory."""

import os
import shutil
import time
from pathlib import Path

from . import detect
from .config import HARNESS_ID

# Forced on every start so the lab runs windowed and keeps running unfocused.
BASE_OVERRIDES = {
    "fullScreen": "false",
    "borderless": "false",
    "focusloss": "false",
    "vsync": "false",
    "tutorialDone": "true",
    "showSurvivalGuide": "false",
    "showFirstTimeSneakTutorial": "false",
    "showFirstTimeSearchTutorial": "false",
    "showFirstAnimalZoneInfo": "false",
}


def ensure_dirs(cfg):
    for d in (cfg.profile_dir, cfg.profile_dir / "mods", cfg.lua_dir, cfg.shots_dir):
        d.mkdir(parents=True, exist_ok=True)


# Options a person must decide themselves; `pzlab init` copies them from that person's own
# Zomboid profile on the same machine when present, never invents them.
PERSONAL_OPTIONS = ("termsOfServiceVersion", "language")


def personal_options_from_user_profile():
    profile_dir = detect.user_profile_dir()
    path = profile_dir / "options.ini" if profile_dir else None
    if not path or not path.exists():
        return {}
    found = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, value = line.partition("=")
        if key in PERSONAL_OPTIONS and value:
            found[key] = value
    return found


def write_options(cfg):
    path = cfg.profile_dir / "options.ini"
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        lines = ["version=1"]
    width, height = cfg["resolution"]
    overrides = dict(BASE_OVERRIDES, width=str(width), height=str(height))
    overrides.update({k: str(v) for k, v in cfg["options_overrides"].items()})
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0]
        if key in overrides:
            out.append("%s=%s" % (key, overrides[key]))
            seen.add(key)
        else:
            out.append(line)
    out += ["%s=%s" % (k, v) for k, v in overrides.items() if k not in seen]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _is_link(path):
    try:
        return bool(os.readlink(path))
    except (OSError, ValueError):
        return False


def _make_link(target, link):
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))  # no admin rights needed
    except ImportError:
        os.symlink(target, link, target_is_directory=True)


def link_mods(cfg, mods_dir=None):
    """Links every configured mod folder into <profile>/mods/<name> (or into mods_dir)."""
    mods_dir = mods_dir or cfg.profile_dir / "mods"
    for name, target in cfg.mods.items():
        detect.mod_root(target)  # validates the layout
        link = mods_dir / name
        if link.exists() or _is_link(link):
            if _is_link(link) and Path(os.path.realpath(link)) == target:
                continue
            if not _is_link(link):
                raise FileExistsError("%s exists and is not a link; refusing to touch it" % link)
            os.rmdir(link)  # removes the link only, never the target
        _make_link(target, link)


def mod_id(cfg, name):
    """Reads id= from the mod's mod.info (version folder first, then common)."""
    for sub in ("42", "common"):
        info = cfg.mods[name] / sub / "mod.info"
        if info.exists():
            for line in info.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().lower().startswith("id="):
                    return line.split("=", 1)[1].strip()
    return name


def write_active_mods(cfg, enabled):
    ids = [HARNESS_ID] + [m for m in enabled if m != HARNESS_ID]
    mods_dir = cfg.profile_dir / "mods"
    # Without this marker the game creates it at boot and empties default.txt.
    (mods_dir / detect.mods_reset_marker(cfg.game_dir)).write_text(
        "If this file does not exist, default.txt will be reset to empty (no mods active).", encoding="utf-8")
    body = "VERSION = 1,\n\nmods\n{\n" + "".join("    mod = %s,\n" % i for i in ids) + "}\n\nmaps\n{\n}\n"
    (mods_dir / "default.txt").write_text(body, encoding="utf-8")
    return ids


def lua_string(text):
    """A Lua expression for the string. Kahlua's compiler turns non-ASCII characters in string
    literals into U+FFFD (even written as decimal escapes), but string.char takes UTF-16 code
    units, so those characters become string.char(...) pieces: ("J" .. string.char(252) .. "rgen")."""
    parts, run, units = [], [], []
    for ch in text:
        if ord(ch) < 128:
            if units:
                parts.append("string.char(%s)" % ", ".join(units))
                units = []
            if ch == "\\":
                run.append("\\\\")
            elif ch == '"':
                run.append('\\"')
            elif ch == "\n":
                run.append("\\n")
            elif ch == "\r":
                run.append("\\r")
            elif ord(ch) < 32 or ord(ch) == 127:
                run.append("\\%03d" % ord(ch))
            else:
                run.append(ch)
        else:
            if run:
                parts.append('"%s"' % "".join(run))
                run = []
            data = ch.encode("utf-16-le")
            units += [str(int.from_bytes(data[i:i + 2], "little")) for i in range(0, len(data), 2)]
    if units:
        parts.append("string.char(%s)" % ", ".join(units))
    if run or not parts:
        parts.append('"%s"' % "".join(run))
    return parts[0] if len(parts) == 1 else "(" + " .. ".join(parts) + ")"


def lua_long_string(text):
    """A Lua long-bracket literal ([==[ ... ]==]) holding text verbatim, no escaping: for Lua
    source passed through the bridge. The newline after the opening bracket is dropped by Lua."""
    level = 0
    while True:
        close = "]" + "=" * level + "]"
        if (text + close).find(close) == len(text):
            return "[" + "=" * level + "[\n" + text + close
        level += 1


def to_lua(value):
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, (list, tuple)):
        return "{" + ", ".join(to_lua(v) for v in value) + "}"
    if isinstance(value, dict):
        return "{" + ", ".join("[%s] = %s" % (lua_string(str(k)), to_lua(v)) for k, v in value.items()) + "}"
    raise TypeError("cannot convert %r to Lua" % (value,))


def write_locations(cfg):
    """The test-location catalogue as a Lua chunk for the harness (PZLab.locations)."""
    from . import locations
    (cfg.lua_dir / "locations.txt").write_text("return " + to_lua(locations.load()) + "\n", encoding="utf-8")


def write_autostart(cfg, world):
    (cfg.lua_dir / "autostart.txt").write_text("return " + to_lua(world) + "\n", encoding="utf-8")


def clear_runtime_files(cfg, lua_dir=None):
    """Bridge files of a previous run (removed before launch, so nothing reads them meanwhile)."""
    lua_dir = lua_dir or cfg.lua_dir
    if not lua_dir.exists():
        return
    for path in lua_dir.iterdir():
        name = path.name
        if (name in ("cmd.txt", "cmd.tmp", "res.json", "state.json", "autostart.txt")
                or (name.startswith("cmd-") and name.endswith((".txt", ".go")))):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def clear_lua_scratch(cfg):
    """Deletes the Lua files of Lab.load_lua (<profile>/lua-scratch/); returns how many."""
    root = cfg.lua_scratch_dir
    if not root.exists() or cfg.profile_dir not in root.resolve().parents:
        return 0
    count = sum(1 for p in root.rglob("*") if p.is_file())
    shutil.rmtree(root, ignore_errors=True)
    return count


def prune_saves(cfg, keep, root=None, worlds=None):
    """Deletes all but the newest `keep` worlds under <profile>/Saves/*/ (root: another profile;
    worlds: an explicit list of world folders to choose from)."""
    root = root or cfg.profile_dir
    saves = root / "Saves"
    if worlds is None:
        if not saves.exists():
            return []
        worlds = [w for mode in saves.iterdir() if mode.is_dir() for w in mode.iterdir() if w.is_dir()]
    worlds = sorted(worlds, key=lambda w: w.stat().st_mtime, reverse=True)
    removed = []
    for w in worlds[keep:]:
        if root in w.resolve().parents:
            shutil.rmtree(w, ignore_errors=True)
            removed.append(w.name)
    return removed


# Lab server profile (the dedicated server's -cachedir) ------------------------------------

# Server/<name>.ini values the lab always sets (lab.json "server.options" goes on top): nothing
# public, no router port mapping (UPnP), no Steam, no backups, no voice, no welcome chat.
LAB_SERVER_OPTIONS = {
    "Public": "false",
    "UPnP": "false",
    "SteamVAC": "false",
    "SteamScoreboard": "false",
    "VoiceEnable": "false",
    "BackupsOnStart": "false",
    "BackupsOnVersionChange": "false",
    "BackupsPeriod": "0",
    "ServerWelcomeMessage": "",
    "Open": "true",
    "Password": "",
    "WorkshopItems": "",
    "RCONPassword": "",
}


def ensure_server_dirs(cfg):
    root = cfg.server_profile_dir
    for d in (root, root / "mods", root / "Server", cfg.server_lua_dir):
        d.mkdir(parents=True, exist_ok=True)


def _update_ini(path, values):
    """Sets key=value lines in an ini-style file (keeps other lines), appending missing keys."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    seen, out = set(), []
    for line in lines:
        key = line.split("=", 1)[0]
        if key in values and not line.startswith("#"):
            out.append("%s=%s" % (key, values[key]))
            seen.add(key)
        else:
            out.append(line)
    out += ["%s=%s" % (k, v) for k, v in values.items() if k not in seen]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def server_options(cfg, mod_ids, world):
    """The Server/<name>.ini values for this start."""
    server = cfg["server"]
    values = dict(LAB_SERVER_OPTIONS)
    values.update({
        "DefaultPort": str(server["port"]),
        "UDPPort": str(server["udp_port"]),
        "Map": world.get("map") or "Muldraugh, KY",
        "Mods": ";".join(mod_ids),
        "SpawnPoint": "%d,%d,%d" % (int(world["x"]), int(world["y"]), int(world.get("z") or 0)),
    })
    values.update({k: _ini_value(v) for k, v in server["options"].items()})
    return values


def _ini_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def write_server_files(cfg, mod_ids, world):
    """Server/<name>.ini (lab options), <name>_SandboxVars.lua (world sandbox overrides on the
    defaults), <name>_spawnregions.lua + <name>_spawnpoints.lua (one region: every new character
    spawns at world x, y, z, so the client skips the spawn-region choice)."""
    name = cfg["server"]["name"]
    folder = cfg.server_profile_dir / "Server"
    values = server_options(cfg, mod_ids, world)
    _update_ini(folder / (name + ".ini"), values)
    version = detect.class_int_constant(cfg.game_dir, "zombie/SandboxOptions", "SANDBOX_VERSION")
    sandbox = dict(world.get("sandbox") or {})
    body = "".join("    [%s] = %s,\n" % (lua_string(str(k)), to_lua(v)) for k, v in sandbox.items())
    # VERSION must match the game's, or the loader "upgrades" the table from version 0.
    (folder / (name + "_SandboxVars.lua")).write_text(
        "SandboxVars = {\n    VERSION = %d,\n%s}\n" % (version or 6, body), encoding="utf-8")
    (folder / (name + "_spawnregions.lua")).write_text(
        "function SpawnRegions()\n    return {\n        { name = %s, serverfile = %s },\n    }\nend\n"
        % (lua_string(values["Map"]), lua_string(name + "_spawnpoints.lua")), encoding="utf-8")
    (folder / (name + "_spawnpoints.lua")).write_text(
        "function SpawnPoints()\n    return {\n        unemployed = { { posX = %d, posY = %d, posZ = %d } },\n"
        "    }\nend\n" % (int(world["x"]), int(world["y"]), int(world.get("z") or 0)), encoding="utf-8")
    return values


def archive_server_world(cfg, keep):
    """Moves the server's world (Saves/Multiplayer/<name>) and user database (db/<name>.db) to
    Saves/Multiplayer/<name>_<time>/, so the next start creates a fresh world; keeps the newest
    `keep` archives. Returns {archived, pruned}."""
    name = cfg["server"]["name"]
    root = cfg.server_profile_dir
    saves = root / "Saves" / "Multiplayer"
    world, db = saves / name, root / "db" / (name + ".db")
    archived = None
    if world.exists() or db.exists():
        archived = saves / ("%s_%s" % (name, time.strftime("%Y%m%d_%H%M%S")))
        archived.mkdir(parents=True, exist_ok=True)
        if world.exists():
            shutil.move(str(world), str(archived / name))
        if db.exists():
            shutil.move(str(db), str(archived / db.name))
    old = [w for w in saves.iterdir() if w.is_dir() and w.name.startswith(name + "_")] if saves.exists() else []
    return {"archived": archived.name if archived else None,
            "pruned": prune_saves(cfg, keep, root=root, worlds=old)}
