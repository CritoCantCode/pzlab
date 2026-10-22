"""Reading the lab's console.txt: marks (byte offsets) and error extraction."""

import re

ERROR_LINE = re.compile(r"^(ERROR|SEVERE)\b|STACK TRACE|Exception|attempted index|\[PZLab\] ERROR|Lua fail")
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
)


class Log:
    def __init__(self, cfg):
        self.path = cfg.console_log
        self.mark_file = cfg.profile_dir / "pzlab.logmark"

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
