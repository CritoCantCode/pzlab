"""Reading the lab's console.txt: marks (byte offsets) and error extraction."""

import re
from pathlib import Path

ERROR_LINE = re.compile(r"^(ERROR|SEVERE)\b|STACK TRACE|Exception|attempted index|\[PZLab\] ERROR|Lua fail")
# ExceptionLogger's block header, e.g. "ERROR: General      f:3510> Lua(Vanilla).onTick> Exception thrown"
EXCEPTION_HEADER = re.compile(r"^[A-Z]+\s*:\s*\S+\s+f:(\d+)(.*?)>\s*Exception thrown\s*$")
# "java.lang.RuntimeException: attempted index: x of non-table: null at KahluaThread.tableget(KahluaThread.java:1430)."
EXCEPTION_LINE = re.compile(r"^(?P<cls>[\w.$]+)(?::\s?(?P<msg>.*?))?(?: at (?P<at>[^\s()]+\([^()]*\)))?\.?$", re.S)
# "Lua(Vanilla).onTick(MyFile.lua:12)", "Lua((MOD:Some Mod)).run(File.lua:95)", "Lua.File.lua(File.lua:2)"
LUA_FRAME = re.compile(r"^Lua(?:\((?P<origin>\(?[^()]*\)?)\))?\.(?P<fn>.+?)\((?P<file>[^()]*?):(?P<line>-?\d+)\)$")
STACK_LINES = 8
# Vanilla noise seen on every launch of 42.21; never caused by mods.
NOISE = (
    "AngelCodeFont failed to load page",
    "FluidContainerScript.load",
    "handleMannequinZone",
    "not adding suspicious zone",
    "mergeRoomsOntoMetaCell",
    "CellLoader> missing tile",
    "CraftRecipeComponentScript",
    "refusing to list",
    "Missing tile definition",  # map data referencing *_TEST_* tiles, logged as areas load (42.21)
    # multiplayer: loading the server's mods, the animator walks every mod's media/AnimSets and
    # media/actiongroups and logs a NoSuchFileException for each folder a mod lacks (42.21)
    "AdvancedAnimator$1.visitFileFailed",
    "AnimSets at WindowsException",
    "actiongroups at WindowsException",
)


class Log:
    """A game log file (the client's console.txt by default) read from a mark (byte offset).
    Log(cfg) = the lab client's console.txt; Log(path=..., mark_file=...) any other log (the lab
    server's, see labkit/server.py)."""

    def __init__(self, cfg=None, path=None, mark_file=None):
        self.path = Path(path) if path else cfg.console_log
        self.mark_file = Path(mark_file) if mark_file else cfg.profile_dir / "pzlab.logmark"

    def size(self):
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0

    def mark(self):
        offset = self.size()
        self.mark_file.write_text(str(offset))
        return offset

    def get_mark(self):
        try:
            offset = int(self.mark_file.read_text())
        except (FileNotFoundError, ValueError):
            return 0
        return offset if offset <= self.size() else 0  # console.txt restarts on each launch

    def lines(self, since=None):
        offset = self.get_mark() if since is None else since
        try:
            with open(self.path, "rb") as f:
                f.seek(offset)
                data = f.read()
        except FileNotFoundError:
            return []
        return data.decode("utf-8", errors="replace").splitlines()

    def errors(self, since=None, include_noise=False):
        """Error lines plus the indented stack-trace lines that follow them."""
        out = []
        capturing = 0
        for line in self.lines(since):
            if ERROR_LINE.search(line) and (include_noise or not any(n in line for n in NOISE)):
                out.append(line)
                capturing = 12
            elif capturing and (line.startswith(("\t", " ", "-")) or "STACK" in line):
                out.append(line)
                capturing -= 1
            else:
                capturing = 0
        return out

    def exceptions(self, since=None):
        """Exceptions the engine logged (ExceptionLogger), one dict per "Exception thrown" block:
        Lua errors (also those caught by pcall, see AGENT.md), files that failed to load, Java
        exceptions. Keys: where (the logger's caller, e.g. "Lua(Vanilla).onTick"), frame (game
        frame), exception (Java class), message, at (Java location), function/file/line (top Lua
        frame, when there is one), stack (up to 8 Lua frames, else the first Java frames)."""
        out = []
        lines = self.lines(since)
        i = 0
        while i < len(lines):
            header = EXCEPTION_HEADER.match(lines[i])
            i += 1
            if not header:
                continue
            rest = header.group(2)
            entry = {"where": rest.split(">", 1)[-1].strip() if ">" in rest else rest.strip(),
                     "frame": int(header.group(1)), "exception": None, "message": None}
            body = []
            while i < len(lines) and lines[i].startswith("\t"):
                body.append(lines[i].strip())
                i += 1
            if body and not body[0].startswith("Stack trace"):
                m = EXCEPTION_LINE.match(body[0])
                if m:
                    entry["exception"], entry["message"] = m.group("cls"), m.group("msg")
                    if m.group("at"):
                        entry["at"] = m.group("at")
                else:
                    entry["message"] = body[0]
            frames = body[body.index("Stack trace:") + 1:] if "Stack trace:" in body else []
            lua = [f for f in frames if LUA_FRAME.match(f)]
            if lua:
                top = LUA_FRAME.match(lua[0])
                entry.update(function=top.group("fn"), file=top.group("file"), line=int(top.group("line")))
            entry["stack"] = (lua or frames)[:STACK_LINES]
            causes = [b for b in body if b.startswith("Caused by:")]
            if causes:
                entry["causes"] = causes
            out.append(entry)
        return out
