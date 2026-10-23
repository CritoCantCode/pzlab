"""Smoke test for fixtures, hovering, inventory panes, drag_row and modifier clicks.

Spawns a floor bag, a corpse and a vehicle around the player, hovers each with the engine's own
pickers, opens the vanilla inventory windows, checks every visible row registers under the
mouse, drags a floor item and a corpse item into the player inventory, Alt+clicks, restores the
UI layout, then clears the fixtures and checks the area and inventory are back to their starting
state.

Run: python pzlab.py run scripts/probe_fixtures.py [X Y Z]
(default: where the player stands; pick an open, lit spot such as a street)."""

import sys
import time

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", name, detail))


if len(sys.argv) >= 4:
    lab.lua("return PZLab.teleport(%s, %s, %s)" % tuple(sys.argv[1:4]))
    time.sleep(1.5)
px, py, pz = lab.lua("local p = PZLab.player() return {math.floor(p:getX()), math.floor(p:getY()), math.floor(p:getZ())}")
census = lab.lua("return #PZLab.findObjects({ radius = 12 })")
inv_count = lab.lua("return PZLab.player():getInventory():getItems():size()")

spawned = lab.lua("""
local px, py, pz = %d, %d, %d
local bag = PZLab.spawnWorldItem("Base.Bag_Schoolbag", px + 1, py + 1, pz,
    { contents = { {"Base.Apple", 2}, "Base.Hammer" } })
local corpse = PZLab.spawnCorpse(px - 1, py + 1, pz, { contents = { {"Base.Bandage", 2}, "Base.WaterBottle" } })
local car = PZLab.spawnVehicle("Base.CarNormal", px + 5, py - 1, pz, "E", { open = true })
local mine = PZLab.addItems(PZLab.player(), { {"Base.Bag_Schoolbag", 1, contents = { {"Base.Apple", 3} }} })
return { bag = PZLab.handle(bag), corpse = PZLab.handle(corpse), car = PZLab.handle(car), mine = PZLab.handle(mine[1]) }
""" % (px, py, pz))
time.sleep(1.0)  # corpse models are drawn a few frames after they are created
print("spawned", spawned, "shot", lab.screenshot())

for kind in ("bag", "corpse", "car"):
    point = lab.hover(spawned[kind])
    check("hover %s" % kind, point is not None, point)

layout = lab.save_layout()  # the windows are put back as they were at the end
inv, loot = lab.lua("return PZLab.openInventoryWindows()")  # vanilla windows, expanded and pinned
time.sleep(0.3)
panes = {p["handle"]: p["owner"]["name"] for p in lab.inventory_panes()}
check("vanilla panes listed", panes.get(inv) == "inventory0" and panes.get(loot) == "loot0", panes)

bad = []
for pane in (inv, loot):
    for row in lab.pane_rows(pane):
        lab.input.move(row["point"]["x"], row["point"]["y"])
        time.sleep(0.12)
        if lab.lua("return PZLab.uiElement(%d).mouseOverOption" % pane) != row["index"]:
            bad.append((pane, row["index"], row["name"]))
check("every visible row registers under the mouse", not bad, bad)


def show_in_loot(container_expr):
    """Clicks the loot window's button for a container (any ISButton whose .inventory is it).
    The window rebuilds its buttons when containers appear, so it retries until the pane shows it."""
    for _ in range(5):
        rect = lab.lua("""
            local c = %s
            for _, b in ipairs(PZLab.findUI(function(el) return type(el) == 'table' and el.inventory == c
                    and PZLab.isA(el, 'ISButton') end, { root = getPlayerLoot(0) })) do return PZLab.rect(b) end
        """ % container_expr)
        if rect:
            lab.input.click(rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2)
            time.sleep(0.3)
            if lab.lua("return getPlayerLoot(0).inventoryPane.inventory == %s" % container_expr):
                return True
        time.sleep(0.5)
    return False


for label, container, full_type in (("floor", "ISInventoryPage.GetFloorContainer(0)", "Base.Bag_Schoolbag"),
                                    ("corpse", "PZLab.get(%d):getContainer()" % spawned["corpse"], "Base.WaterBottle")):
    if not show_in_loot(container):
        check("drag from %s" % label, False, "loot window never showed the container")
        continue
    rows = [r for r in lab.pane_rows(loot) if r["fullType"] == full_type]
    if not rows:
        check("drag from %s" % label, False, "no %s row in the loot pane" % full_type)
        continue
    done = lab.drag_row(loot, rows[0]["index"], inv)
    lab.wait_until("not PZLab.playerBusy()", timeout_s=20)
    check("drag from %s" % label, lab.lua("return PZLab.get(%d):isInPlayerInventory()" % rows[0]["handle"]),
          "%s, actions after drop: %s" % (rows[0]["name"], done["actions"]))

lab.lua("PZLab.recordInput(true) return true")
drop = lab.lua("return PZLab.paneInfo(%d)" % inv)["drop"]
lab.input.click(drop["x"], drop["y"], modifiers=("alt",))
events = lab.lua("return PZLab.inputEvents()") or []
check("alt held during click", bool(events) and all(e["alt"] for e in events), events)
lab.lua("PZLab.recordInput(false) return true")
lab.input.move(5, 5)
restored = lab.restore_layout(layout)
KEYS = ("x", "y", "w", "h", "visible", "pinned", "collapsed")
pages = lambda snap: [{k: w.get(k) for k in KEYS} for w in snap["windows"] if w["type"] == "ISInventoryPage"]
check("UI layout restored", pages(lab.save_layout()) == pages(layout),
      [c for c in restored["changed"] if c["type"] == "ISInventoryPage"])

removed = lab.clear_fixtures()
time.sleep(0.5)
check("clearFixtures leaves the area as it was",
      lab.lua("return #PZLab.findObjects({ radius = 12 })") == census, "removed %s fixtures" % removed)
check("clearFixtures restores the inventory",
      lab.lua("return PZLab.player():getInventory():getItems():size()") == inv_count)
print("%d/%d passed" % (sum(results), len(results)))
