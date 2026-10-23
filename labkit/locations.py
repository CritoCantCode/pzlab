"""Catalogue of well-known test locations on the vanilla map (labkit/locations.json).

Each entry: {"x", "y", "z", "map", "verified" (game version it was checked on), "about",
"building" ([x1, y1, x2, y2] when it is one), "contents" (what is there, per floor)}.
Coordinates are tiles; whole numbers mean the tile centre. Setup writes the catalogue into the
profile so the harness has it as PZLab.locations."""

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent / "locations.json"


def load():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    return {name: entry for name, entry in data.items() if not name.startswith("_")}


def get(name):
    locations = load()
    if name not in locations:
        raise ValueError("unknown location '%s' (known: %s)" % (name, ", ".join(sorted(locations))))
    return locations[name]


def position(target):
    """A location name, or (x, y[, z]) numbers -> {"x", "y", "z"} for a world start / teleport."""
    if isinstance(target, str):
        entry = get(target)
        return {"x": entry["x"], "y": entry["y"], "z": entry.get("z", 0)}
    values = list(target)
    if len(values) not in (2, 3):
        raise ValueError("give a location name or X Y [Z], got %r" % (target,))
    x, y = values[0], values[1]
    z = values[2] if len(values) == 3 else 0
    return {"x": x, "y": y, "z": z}
