"""Discovery of the installed game and of facts that change between game versions, read from the
install itself so nothing is hardcoded to one machine or one build."""

import json
import os
import platform
import re
import zipfile
from pathlib import Path

JAR = "projectzomboid.jar"


def _is_game_dir(p):
    return p is not None and (Path(p) / JAR).exists()


def _steam_roots():
    roots = []
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam")):
            for name in ("SteamPath", "InstallPath"):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        roots.append(Path(winreg.QueryValueEx(k, name)[0]))
                except OSError:
                    pass
    except ImportError:
        pass
    roots += [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam",
              Path.home() / ".steam" / "steam", Path.home() / ".local" / "share" / "Steam"]
    return roots


def find_game_dir():
    """Project Zomboid install dir from Steam's library folders, or None."""
    libraries = []
    for root in _steam_roots():
        vdf = root / "steamapps" / "libraryfolders.vdf"
        libraries.append(root)
        if vdf.exists():
            text = vdf.read_text(encoding="utf-8", errors="replace")
            libraries += [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', text)]
    for lib in libraries:
        candidate = lib / "steamapps" / "common" / "ProjectZomboid"
        if _is_game_dir(candidate):
            return candidate.resolve()
    return None


def launch_spec(game_dir):
    """JVM args, classpath and main class from the game's own ProjectZomboid64.json."""
    spec_file = Path(game_dir) / "ProjectZomboid64.json"
    spec = json.loads(spec_file.read_text(encoding="utf-8")) if spec_file.exists() else {}
    vm_args = list(spec.get("vmArgs", []))
    # OS-version-specific extras, e.g. {"windows": {"6.1": {...G1GC}, "10.0.17134": {...ZGC}}}:
    # only the highest entry not above the running OS applies (they pick conflicting GCs).
    os_key = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(platform.system())
    current = _version_tuple(platform.version())
    matching = [(v, extra) for v, extra in spec.get(os_key, {}).items() if _version_tuple(v) <= current]
    if matching:
        vm_args += max(matching, key=lambda kv: _version_tuple(kv[0]))[1].get("vmArgs", [])
    vm_args = [a for a in vm_args if not a.startswith("-Dzomboid.steam=")] + ["-Dzomboid.steam=0"]
    return {
        "vm_args": vm_args,
        "classpath": spec.get("classpath", [".", JAR]),
        "main_class": spec.get("mainClass", "zombie/gameStates/MainScreenState").replace("/", "."),
    }


def _version_tuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", s)[:4])


def mods_reset_marker(game_dir):
    """Name of the marker file whose absence makes the game empty mods/default.txt at boot.

    GameWindow calls ZomboidFileSystem.resetDefaultModsForNewRelease("<NN_NN>") which creates
    mods/reset-mods-<NN_NN>.txt; the argument is the only NN_NN string constant in GameWindow."""
    try:
        with zipfile.ZipFile(Path(game_dir) / JAR) as jar:
            data = jar.read("zombie/GameWindow.class")
        found = sorted(set(m.decode() for m in re.findall(rb"\x01\x00\x05(\d\d_\d\d)", data)))
        if len(found) == 1:
            return "reset-mods-%s.txt" % found[0]
    except (OSError, KeyError, zipfile.BadZipFile):
        pass
    return "reset-mods-42_00.txt"


def mod_root(path):
    """Accepts a mod folder (has common/ or a version folder) or a Workshop item
    (Contents/mods/<one mod>) and returns the mod folder."""
    path = Path(path)
    if (path / "common").is_dir() or any(c.is_dir() and re.match(r"^\d+(\.\d+)*$", c.name) for c in path.iterdir()):
        return path
    inner = path / "Contents" / "mods"
    if inner.is_dir():
        mods = [c for c in inner.iterdir() if c.is_dir()]
        if len(mods) == 1:
            return mod_root(mods[0])
        raise ValueError("%s contains several mods (%s); pass one of them" % (inner, ", ".join(m.name for m in mods)))
    raise ValueError("%s is not a mod folder (no common/ or version folder)" % path)


def user_profile_dir():
    """The player's own Zomboid folder on this machine (default location), if it exists."""
    p = Path.home() / "Zomboid"
    return p if p.is_dir() else None
