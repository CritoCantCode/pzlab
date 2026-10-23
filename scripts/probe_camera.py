"""Smoke test for camera zoom and picking across zoom levels, and for hovering under UI.

For each zoom level (default 0.75 1 1.5): lab.set_zoom, check the projection (a tile 4 east of
the player lands 4 * 64 / zoom UI pixels to the right), then at a spawned vehicle's and corpse's
projected centre compare the picker conventions (PZLab.pickVehicle / pickCorpse take UI pixels;
raw IsoObjectPicker.PickVehicle needs UI pixels x zoom, PickCorpse plain UI pixels) and hover both
with lab.hover. Then puts the vanilla inventory window over the corpse and checks lab.hover skips
the covered points (reported in "covered"). Restores zoom and UI layout, clears the fixtures.

Run: python pzlab.py run scripts/probe_camera.py [Z ...]
Needs open ground within 40 tiles for the vehicle (PZLab.findOpenArea), e.g. after
`python pzlab.py goto parking-lot`. Keep the real mouse cursor off the game window."""

import sys
import time

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", name, detail))


levels = [float(z) for z in sys.argv[1:]] or [0.75, 1.0, 1.5]
lab.ensure_playable()
start_zoom = lab.get_zoom()

area = lab.lua("return PZLab.findOpenArea(7, 10, { radius = 40 })")
if not area:
    raise SystemExit("no open ground within 40 tiles: goto an open area first")
# In the 7 x 10 area: the player at its west side, a corpse to the north, a north-south vehicle
# three tiles east (all on screen at every zoom level tested).
lab.goto([area["x"] + 2, area["y"] + 5, 0])
spawned = lab.lua("""
local car = PZLab.spawnVehicle("Base.CarNormal", %d, %d, 0, "S")
local body = PZLab.spawnCorpse(%d, %d, 0)
return { car = PZLab.handle(car), body = PZLab.handle(body) }
""" % (area["x"] + 5, area["y"] + 5, area["x"] + 2, area["y"] + 1))
time.sleep(1.0)  # corpse models appear a few frames after creation

PICKS = """
local car, body, zoom = PZLab.get(%d), PZLab.get(%d), PZLab.getZoom()
local P = IsoObjectPicker.Instance
local function at(obj, aim)
    local s = aim or PZLab.toScreen(obj:getX(), obj:getY(), obj:getZ())
    return math.floor(s.x), math.floor(s.y)
end
local cx, cy = at(car)
local carAim = PZLab.aimPoints(car, 1)[1]
local bx, by = at(body, PZLab.aimPoints(body, 1)[1])
local sq = PZLab.player():getSquare()
local a, b = PZLab.toScreen(sq:getX(), sq:getY(), 0), PZLab.toScreen(sq:getX() + 4, sq:getY(), 0)
return {
    zoom = zoom, tile4 = b.x - a.x,
    carCentre = { cx, cy },
    vehicle = { helper = PZLab.pickVehicle(cx, cy) == car,
                rawPlain = P:PickVehicle(cx, cy) == car,
                rawScaled = P:PickVehicle(math.floor(cx * zoom), math.floor(cy * zoom)) == car,
                aimPoint = carAim ~= nil },
    corpse = { helper = PZLab.pickCorpse(bx, by) == body,
               rawPlain = P:PickCorpse(bx, by) == body,
               rawScaled = P:PickCorpse(math.floor(bx * zoom), math.floor(by * zoom)) == body },
}
""" % (spawned["car"], spawned["body"])

for z in levels:
    got = lab.set_zoom(z)
    r = lab.lua(PICKS)
    check("zoom %.2f set" % z, abs(got - z) < 1e-3 and abs(r["zoom"] - z) < 1e-3, got)
    check("zoom %.2f projection" % z, abs(r["tile4"] - 256 / z) < 0.5, "4 tiles east = %.1f px" % r["tile4"])
    v, c = r["vehicle"], r["corpse"]
    check("zoom %.2f vehicle picker" % z, v["helper"] and v["rawScaled"] and (z == 1 or not v["rawPlain"]),
          "centre %s %s" % (r["carCentre"], v))
    check("zoom %.2f corpse picker" % z, c["helper"] and c["rawPlain"] and (z == 1 or not c["rawScaled"]), c)
    for kind in ("car", "body"):
        point = lab.hover(spawned[kind])
        check("zoom %.2f hover %s" % (z, kind), point is not None, point)

# Hovering under UI: the inventory window placed over the corpse.
lab.set_zoom(1)
with lab.ui_layout():
    lab.input.move(5, 5)
    body_pt = lab.lua("local b = PZLab.get(%d) return PZLab.aimPoints(b, 1)[1]" % spawned["body"])
    lab.show_inventory_panes(inventory={"x": body_pt["x"] - 150, "y": body_pt["y"] - 150, "w": 300, "h": 300})
    time.sleep(0.2)
    point = lab.hover(spawned["body"])
    report = lab.last_hover
    check("covered aim points skipped and reported",
          any(p["ui"] == "ISInventoryPage" for p in report["covered"]) and report["tried"] == 0, report)
    check("no hover claimed under the window",
          point is None or not lab.lua("return PZLab.pickBlockedBy(%d, %d)" % (point["x"], point["y"])), point)
    lab.input.move(5, 5)
    check("isHovered false under UI, true with allowUI", lab.lua(
        "local b = PZLab.get(%d) return { PZLab.isHovered(b, %d, %d), PZLab.isHovered(b, %d, %d, true) }"
        % (spawned["body"], body_pt["x"], body_pt["y"], body_pt["x"], body_pt["y"])) == [False, True])

lab.input.move(5, 5)
lab.set_zoom(start_zoom)
lab.clear_fixtures()
print("%d/%d passed" % (sum(results), len(results)))
