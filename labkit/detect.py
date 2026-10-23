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


SERVER_MAIN = "zombie.network.GameServer"


def server_launch_spec(game_dir):
    """JVM args, classpath and main class for a dedicated server from this install:
    a dedicated-server install's ProjectZomboid64.json (mainClass GameServer), else the java line
    of the install's server launcher (ProjectZomboidServer.bat / start-server.sh), else the
    client's spec with the server main class. Steam stays off (-Dzomboid.steam=0)."""
    game_dir = Path(game_dir)
    spec = launch_spec(game_dir)
    if spec["main_class"] == SERVER_MAIN:
        return dict(spec, source="ProjectZomboid64.json")
    for name in ("ProjectZomboidServer.bat", "StartServer64.bat", "start-server.sh", "ProjectZomboidServer.sh"):
        script = game_dir / name
        if script.exists():
            parsed = _parse_server_script(script.read_text(encoding="utf-8", errors="replace"))
            if parsed:
                return dict(parsed, source=name)
    return dict(spec, main_class=SERVER_MAIN, source="ProjectZomboid64.json (client spec, server main class)")


def _parse_server_script(text):
    """{vm_args, classpath, main_class} from the java command line of a server launcher script."""
    variables = {}
    for m in re.finditer(r"^\s*(?:@?set|export)\s+(\w+)=(.*)$", text, re.M | re.I):
        variables[m.group(1).upper()] = m.group(2).strip().strip('"')
    for line in text.splitlines():
        if SERVER_MAIN not in line or "java" not in line.lower():
            continue
        tokens = re.findall(r'"[^"]*"|\S+', line)
        tokens = [t.strip('"') for t in tokens]
        java = next((i for i, t in enumerate(tokens) if re.search(r"java(\.exe)?$", t, re.I)), None)
        if java is None or "-cp" not in tokens[java:]:
            continue
        cp_at = tokens.index("-cp", java)
        classpath = re.sub(r"%(\w+)%|\$\{?(\w+)\}?",
                           lambda m: variables.get((m.group(1) or m.group(2)).upper(), ""), tokens[cp_at + 1])
        main = tokens[cp_at + 2] if cp_at + 2 < len(tokens) else SERVER_MAIN
        vm_args = [t for t in tokens[java + 1:cp_at] if t.startswith("-")]
        seen, unique = set(), []
        for a in vm_args:  # the 42.21 launcher lists -XX:+UseZGC twice
            if a not in seen:
                seen.add(a)
                unique.append(a)
        vm_args = [a for a in unique if not a.startswith("-Dzomboid.steam=")] + ["-Dzomboid.steam=0"]
        return {"vm_args": vm_args, "classpath": [c for c in classpath.split(";" if ";" in classpath else ":") if c] or [".", JAR],
                "main_class": main.replace("/", ".")}
    return None


def class_int_constant(game_dir, class_name, field):
    """A static final int field's compile-time value (ConstantValue attribute) read from a class in
    the game jar, e.g. ("zombie/SandboxOptions", "SANDBOX_VERSION"); None when not found."""
    import struct
    try:
        with zipfile.ZipFile(Path(game_dir) / JAR) as jar:
            data = jar.read(class_name + ".class")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    pos, pool = 10, [None]
    count = struct.unpack(">H", data[8:10])[0]
    while len(pool) < count:
        tag = data[pos]
        if tag == 1:
            n = struct.unpack(">H", data[pos + 1:pos + 3])[0]
            pool.append(data[pos + 3:pos + 3 + n].decode("utf-8", "replace"))
            pos += 3 + n
        elif tag in (3, 4):
            pool.append(struct.unpack(">i", data[pos + 1:pos + 5])[0] if tag == 3 else None)
            pos += 5
        elif tag in (5, 6):
            pool += [None, None]
            pos += 9
        elif tag in (7, 8, 16, 19, 20):
            pool.append(None)
            pos += 3
        elif tag == 15:
            pool.append(None)
            pos += 4
        elif tag in (9, 10, 11, 12, 17, 18):
            pool.append(None)
            pos += 5
        else:
            return None
    pos += 6  # access, this, super
    interfaces = struct.unpack(">H", data[pos:pos + 2])[0]
    pos += 2 + 2 * interfaces
    fields = struct.unpack(">H", data[pos:pos + 2])[0]
    pos += 2
    for _ in range(fields):
        _, name_i, _, attrs = struct.unpack(">HHHH", data[pos:pos + 8])
        pos += 8
        for _ in range(attrs):
            attr_name, length = struct.unpack(">HI", data[pos:pos + 6])
            if pool[name_i] == field and pool[attr_name] == "ConstantValue":
                value = pool[struct.unpack(">H", data[pos + 6:pos + 8])[0]]
                return value if isinstance(value, int) else None
            pos += 6 + length
    return None


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
