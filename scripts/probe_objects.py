"""World objects and B42 entities as fixtures: PZLab.spawnObject and PZLab.spawnEntity.

Checks (PASS/FAIL; the exit status is 1 when one fails), on open ground near the parking-lot
location:
  - spawnObject per sprite: the Java class and containers the map loader gives it, and with
    placed = true what a player's moveable placement gives; whole multi-tile sprites; a facing;
    contents by container type;
  - spawnEntity per example entity: every part has the entity script, the master the CraftBench
    (where the script has one) and the slaves point at the master; a script OnCreate that
    replaces the object (ButcherHook); an entity without a build recipe (WaterDispenser);
  - a Forge from spawnEntity equals one built through the build menu's own path
    (ISBuildIsoEntity:tryBuild -> ISBuildAction -> create, with build cheat): class, components,
    modData, health and thumpable flags, master/slave;
  - refusals: unknown sprite or entity script, unloaded square, a spot vanilla's build check
    refuses, a missing face;
  - nothing logged; clearFixtures leaves every square as it was.
Re-run after a game update.

Run: python pzlab.py run scripts/probe_objects.py   (single player, in a world)"""

import sys
import time

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", name, detail))


lab.ensure_playable()
lab.goto("parking-lot")
lab.clear_fixtures()
area = lab.lua("return PZLab.findOpenArea(13, 12, { radius = 30 })")
if not area:
    check("open ground for the probe (13 x 12 tiles within 30)", False)
    sys.exit(1)
OX, OY = area["x"], area["y"]
lab.lua("PZLab.teleport(%d, %d, 0) return true" % (OX + 6, OY + 12))
time.sleep(1.0)

lab.lua(r'''
PZLabProbe = {}
function PZLabProbe.d(o)
  local sq = o:getSquare()
  local d = { class = getClassSimpleName(o), sprite = o:getSpriteName(), at = sq and (sq:getX() .. "," .. sq:getY()) or "none",
    containers = {}, comps = {}, md = {} }
  for c = 0, o:getContainerCount() - 1 do
    local k = o:getContainerByIndex(c)
    d.containers[#d.containers + 1] = k:getType() .. "=" .. k:getItems():size()
  end
  d.containers = table.concat(d.containers, " ")
  for i = 0, o:componentSize() - 1 do d.comps[#d.comps + 1] = tostring(o:getComponentForIndex(i):getComponentType()) end
  table.sort(d.comps)
  d.comps = table.concat(d.comps, " ")
  for k, v in pairs(o:getModData()) do if k ~= "pzlabFixture" then d.md[#d.md + 1] = tostring(k) .. "=" .. tostring(v) end end
  table.sort(d.md)
  d.md = table.concat(d.md, " ")
  d.entity = o:getEntityScript() and o:getEntityScript():getFullName() or false
  d.craftBench = o:hasComponent(ComponentType.CraftBench)
  if instanceof(o, "IsoThumpable") then
    d.thump = table.concat({ o:getHealth() .. "/" .. o:getMaxHealth(), tostring(o:isThumpable()), tostring(o:isBlockAllTheSquare()),
      tostring(o:isCanPassThrough()), tostring(o:isDismantable()), tostring(o:getNorth()), tostring(o:getBreakSound()), tostring(o:getName()) }, ",")
  end
  local sc = o:getSpriteConfig()
  d.master = sc and sc:isMultiSquareMaster() or false
  d.slave = sc and sc:isMultiSquareSlave() or false
  d.masterSprite = sc and sc:getMultiSquareMaster() and sc:getMultiSquareMaster():getSpriteName() or false
  return d
end
function PZLabProbe.all(obj, parts)
  local out = { obj = PZLabProbe.d(obj), parts = {} }
  for _, p in ipairs(parts) do out.parts[#out.parts + 1] = PZLabProbe.d(p) end
  return out
end
function PZLabProbe.census(x1, y1, x2, y2)
  local t = {}
  for x = x1, x2 do for y = y1, y2 do
    local sq = PZLab.square(x, y, 0)
    t[#t + 1] = sq and (sq:getObjects():size() .. "/" .. sq:getStaticMovingObjects():size()) or "-"
  end end
  return t
end
return true''')
CENSUS = "return PZLabProbe.census(%d, %d, %d, %d)" % (OX - 1, OY - 1, OX + 13, OY + 12)
census = lab.lua(CENSUS)
off = lab.log.size()

OBJECTS = [  # sprite, dx, dy, opts, class, containers (None: not checked), parts
    ("fixtures_sinks_01_9", 0, 1, "{}", "IsoObject", "", 1),
    ("lighting_indoor_02_27", 1, 1, "{}", "IsoLightSwitch", "", 1),
    ("furniture_seating_indoor_01_8", 2, 1, "{}", "IsoObject", "", 1),
    ("appliances_cooking_01_9", 3, 1, "{ contents = { 'Base.Pot' } }", "IsoStove", "stove=1", 1),
    ("appliances_refrigeration_01_0", 4, 1, "{ contents = { fridge = { {'Base.Apple', 2} }, freezer = { 'Base.Icecream' } } }",
     "IsoObject", "fridge=2 freezer=1", 1),
    ("appliances_television_01_3", 5, 1, "{}", "IsoTelevision", "", 1),
    ("location_shop_mall_01_78", 6, 1, "{}", "IsoMannequin", None, 1),  # its container holds the outfit it wears
    ("appliances_radio_01_0", 7, 1, "{}", "IsoRadio", "", 1),
    ("appliances_cooking_01_24", 8, 1, "{}", "IsoStove", "microwave=0", 1),
    ("appliances_cooking_01_35", 9, 1, "{}", "IsoBarbecue", "barbecue=0", 1),
    ("location_business_machinery_01_16", 0, 3, "{ whole = true }", "IsoObject", "", 2),
    ("furniture_tables_high_01_8", 3, 3, "{ whole = true }", "IsoObject", "", 2),
    ("furniture_bedding_01_0", 6, 4, "{ whole = true }", "IsoObject", "", 2),
    ("furniture_seating_indoor_01_8", 8, 3, "{ placed = true }", "IsoThumpable", "", 1),
    ("appliances_refrigeration_01_0", 9, 3, "{ placed = true, contents = { 'Base.Apple' } }", "IsoThumpable", "fridge=1 freezer=0", 1),
    ("fixtures_sinks_01_9", 10, 3, "{ face = 'E' }", "IsoObject", "", 1),
]
for sprite, dx, dy, opts, cls, conts, nparts in OBJECTS:
    r = lab.lua("return PZLabProbe.all(PZLab.spawnObject(%r, %d, %d, 0, %s))" % (sprite, OX + dx, OY + dy, opts))
    o, parts = r["obj"], r["parts"]
    ok = len(parts) == nparts and all(p["class"] == cls for p in parts) and (conts is None or o["containers"] == conts)
    check("spawnObject %s %s" % (sprite, opts), ok, "%s [%s] %s" % (o["class"], o["containers"], [(p["sprite"], p["at"]) for p in parts]))

ENTITIES = [  # script, dx, dy, opts, parts, CraftBench on the master, class of the master
    ("Base.Forge", 0, 6, "{ face = 'S' }", 3, True, "IsoThumpable"),
    ("Base.Kiln_Small", 4, 6, "{}", 1, True, "IsoThumpable"),
    ("Base.Chopping_Block", 6, 6, "{}", 1, True, "IsoThumpable"),
    ("Base.Grindstone", 8, 6, "{}", 1, True, "IsoThumpable"),
    ("Base.Stone_Quern", 10, 6, "{}", 1, True, "IsoThumpable"),
    ("Base.Advanced_Forge", 0, 8, "{ face = 'S' }", 5, True, "IsoThumpable"),  # an L of 5 squares in a 3 x 3 face
    ("Base.Kiln_Large", 4, 8, "{ face = 'S' }", 2, True, "IsoThumpable"),
    ("Base.Pottery_Wheel", 6, 8, "{ face = 'S' }", 2, True, "IsoThumpable"),
    ("Base.SimpleLoom", 8, 8, "{ face = 'S' }", 2, True, "IsoThumpable"),
    ("Base.Spinning_Wheel", 10, 8, "{}", 1, True, "IsoThumpable"),
    ("Base.Drying_Rack", 11, 10, "{ face = 'E' }", 4, False, "IsoThumpable"),
    ("Base.ButcherHook", 4, 11, "{}", 1, False, "IsoButcherHook"),  # its OnCreate replaces the thumpable
    ("Base.WaterDispenser", 6, 11, "{}", 1, False, "IsoObject"),  # no build recipe: placed as the map does
    ("Base.Forge", 12, 3, "{ face = 'E' }", 3, True, "IsoThumpable"),
]
for name, dx, dy, opts, nparts, bench, cls in ENTITIES:
    r = lab.lua("return PZLabProbe.all(PZLab.spawnEntity(%r, %d, %d, 0, %s))" % (name, OX + dx, OY + dy, opts))
    o, parts = r["obj"], r["parts"]
    ok = len(parts) == nparts and o["craftBench"] == bench and o["class"] == cls
    if cls != "IsoButcherHook":
        ok = ok and all(p["entity"] == name for p in parts)
    if nparts > 1:
        ok = ok and o["master"] and all(p["slave"] and p["masterSprite"] == o["sprite"] for p in parts if p["at"] != o["at"])
    check("spawnEntity %s %s" % (name, opts), ok, "master %s %s at %s, %s" % (o["sprite"], o["class"], o["at"], o["comps"]))

# the build menu's own path: tryBuild -> ISBuildAction -> ISBuildIsoEntity:create, build cheat on
lab.lua('''local p = getSpecificPlayer(0)
PZLabProbe.cheat = { p:isBuildCheat(), ISBuildMenu.cheat }
p:setBuildCheat(true) ISBuildMenu.cheat = true
local build = ISBuildIsoEntity:new(p, SpriteConfigManager.GetObjectInfo("Forge"), 4, ISInventoryPaneContextMenu.getContainers(p))
build.player = 0
build:getSprite()
build:tryBuild(%d, %d, 0)
return true''' % (OX + 8, OY + 11))
try:
    lab.wait_until("not PZLab.playerBusy()", timeout_s=60)
finally:
    lab.lua("local p = getSpecificPlayer(0) p:setBuildCheat(PZLabProbe.cheat[1]) ISBuildMenu.cheat = PZLabProbe.cheat[2] return true")
time.sleep(0.5)
for i in range(3):
    top = "local sq = PZLab.square(%d, %d, 0) return PZLabProbe.d(sq:getObjects():get(sq:getObjects():size() - 1))"
    vanilla = lab.lua(top % (OX + 8 + i, OY + 11))
    ours = lab.lua(top % (OX + i, OY + 6))
    vanilla.pop("at"), ours.pop("at")
    check("Forge part %d equals a build through vanilla's build action" % i, vanilla == ours,
          "" if vanilla == ours else "\n   vanilla %s\n   ours    %s" % (vanilla, ours))

exc = lab.log.exceptions(since=off)
check("nothing logged while spawning", not exc, [(e["message"], e["at"]) for e in exc][:3])
lab.lua("getSpecificPlayer(0):setDir(IsoDirections.N) return true")
time.sleep(0.5)
print("screenshot", lab.screenshot())

for code, needle in [
    ("PZLab.spawnObject('no_such_sprite_01_0', %d, %d, 0)" % (OX + 12, OY), "unknown sprite"),
    ("PZLab.spawnEntity('Base.NoSuchEntity', %d, %d, 0)" % (OX + 12, OY), "unknown entity"),
    ("PZLab.spawnObject('fixtures_sinks_01_9', 1000, 1000, 0)", "no loaded square"),
    ("PZLab.spawnEntity('Base.Forge', 1000, 1000, 0)", "no loaded square"),
    ("PZLab.spawnEntity('Base.Forge', %d, %d, 0, { face = 'S' })" % (OX, OY + 6), "build check"),
    ("PZLab.spawnEntity('Base.Forge', %d, %d, 0, { face = 'N' })" % (OX + 12, OY), "no face"),
    ("PZLab.spawnObject('location_shop_mall_01_78', %d, %d, 0, { face = 'N' })" % (OX + 12, OY), "no face"),
]:
    try:
        lab.lua(code)
        check("refused: " + needle, False, "no error")
    except Exception as e:
        check("refused: " + needle, needle in str(e), str(e)[:140])

# the vanilla-built Forge is not a fixture
lab.lua('''for x = %d, %d do local sq = PZLab.square(x, %d, 0) local o = sq:getObjects():get(sq:getObjects():size() - 1)
  if instanceof(o, "IsoThumpable") then sq:transmitRemoveItemFromSquare(o) if sq:getObjects():contains(o) then sq:RemoveTileObject(o) end end end
return true''' % (OX + 8, OX + 10, OY + 11))
off = lab.log.size()
removed = lab.clear_fixtures()
time.sleep(0.5)
check("clearFixtures leaves every square as it was", lab.lua(CENSUS) == census and lab.lua("return #PZLab.fixtures()") == 0,
      "removed %s" % removed)
exc = lab.log.exceptions(since=off)
check("nothing logged while clearing", not exc, [(e["message"], e["at"]) for e in exc][:3])
print("%d/%d passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
