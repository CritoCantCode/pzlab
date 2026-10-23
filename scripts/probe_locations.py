"""Re-verifies the test-location catalogue (labkit/locations.json) against the running game.

For each location (all, or the names given): lab.goto it, check the player stands on the point,
count tile objects with containers per floor inside its "area" and compare with "containers",
check the metagrid rooms listed in "rooms" exist there, check an open area is still clear
(entries with "containers": {"z0": 0}), and take a screenshot (path printed: look at it).
Run after a game update; then update "verified" in the catalogue.

Run: python pzlab.py run scripts/probe_locations.py [NAME ...]"""

import sys

results = []

COUNT = """
local x1, y1, x2, y2 = %d, %d, %d, %d
local counts = {}
for z = 0, 3 do
    local n = 0
    for y = y1, y2 do for x = x1, x2 do
        local sq = PZLab.square(x, y, z)
        if sq then
            local objects = sq:getObjects()
            for i = 0, objects:size() - 1 do
                if objects:get(i):getContainerCount() > 0 then n = n + 1 end
            end
        end
    end end
    if n > 0 then counts["z" .. z] = n end
end
local rooms = {}
local b = getWorld():getMetaGrid():getBuildingAt(%d, %d)
if b then
    local list = b:getRooms()
    for i = 0, list:size() - 1 do
        local r = list:get(i)
        local key = "z" .. r:getZ()
        rooms[key] = rooms[key] or {}
        table.insert(rooms[key], r:getName())
    end
end
return { counts = counts, rooms = rooms }
"""


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", name, detail))


catalogue = lab.locations()
names = sys.argv[1:] or list(catalogue)
start_zoom = lab.get_zoom()
lab.set_zoom(1)
for name in names:
    loc = catalogue[name]
    arrived = lab.goto(name)
    p = arrived["player"]
    check("%s: standing at %s,%s,%s" % (name, loc["x"], loc["y"], loc["z"]),
          (int(p["x"]), int(p["y"]), int(p["z"])) == (loc["x"], loc["y"], loc["z"]), "%d ms" % arrived["ms"])
    x1, y1, x2, y2 = loc["area"]
    found = lab.lua(COUNT % (x1, y1, x2, y2, loc["x"], loc["y"]))
    counts = found["counts"] or {}
    expected = {k: v for k, v in loc.get("containers", {}).items() if v}
    check("%s: containers per floor" % name, counts == expected, "found %s, catalogue %s" % (counts, expected))
    for floor, wanted in loc.get("rooms", {}).items():
        have = (found["rooms"] or {}).get(floor, [])
        missing = sorted(set(wanted) - set(have))
        check("%s: rooms on %s" % (name, floor), not missing, "missing %s" % missing if missing else "")
    if loc.get("containers") == {"z0": 0}:
        blocked = lab.lua("""local out = {}
            for y = %d, %d do for x = %d, %d do
                if not PZLab.isOpenGround(x, y, 0) then out[#out + 1] = x .. "," .. y end
            end end
            return out""" % (y1, y2, x1, x2)) or []
        check("%s: %dx%d open ground" % (name, x2 - x1 + 1, y2 - y1 + 1), not blocked, "blocked: %s" % blocked)
    lab.input.move(5, 700)
    print("     screenshot:", lab.screenshot())
lab.set_zoom(start_zoom)
print("%d/%d passed" % (sum(results), len(results)))
