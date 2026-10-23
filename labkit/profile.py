"""Prepares the lab profile (the game's -cachedir): options, mod links, active mods,
autostart request. Everything written here stays inside the profile directory."""

import os
import shutil
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


def link_mods(cfg):
    """Links every configured mod folder into <profile>/mods/<name>."""
    mods_dir = cfg.profile_dir / "mods"
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


def clear_runtime_files(cfg):
    """Bridge files of a previous run (removed before launch, so nothing reads them meanwhile)."""
    if not cfg.lua_dir.exists():
        return
    for path in cfg.lua_dir.iterdir():
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


def prune_saves(cfg, keep):
    """Deletes all but the newest `keep` worlds under <profile>/Saves/*/."""
    saves = cfg.profile_dir / "Saves"
    if not saves.exists():
        return []
    worlds = [w for mode in saves.iterdir() if mode.is_dir() for w in mode.iterdir() if w.is_dir()]
    worlds.sort(key=lambda w: w.stat().st_mtime, reverse=True)
    removed = []
    for w in worlds[keep:]:
        if cfg.profile_dir in w.resolve().parents:
            shutil.rmtree(w, ignore_errors=True)
            removed.append(w.name)
    return removed
