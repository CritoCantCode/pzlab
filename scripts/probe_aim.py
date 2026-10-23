"""Checks Lab.hover against the engine's own picking for every container object near the
player, then puts a click-through panel (a visible top-level element that lets clicks reach the
world) over the first hovered container: the engine picks nothing under it, so PZLab.uiAt (clicks)
ignores it while PZLab.pickBlockedBy, isHovered, aimPoints and Lab.hover treat it as covering.
Then the same panel with a child over the point, consuming mouse events or not: picking is
blocked either way; left clicks on the container reach the world only through a click-through
child (and every onMouseDown on the way runs).
Run: python pzlab.py run scripts/probe_aim.py"""

import json
import time

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", name, detail))


lab.ensure_playable()
objects = lab.lua("""
local out = {}
local p = PZLab.player()
local px, py, pz = math.floor(p:getX()), math.floor(p:getY()), math.floor(p:getZ())
for dy = -4, 4 do for dx = -4, 4 do
    local sq = PZLab.square(px + dx, py + dy, pz)
    if sq then
        local objs = sq:getObjects()
        for j = 0, objs:size() - 1 do
            local o = objs:get(j)
            if o:getContainerCount() > 0 then
                out[#out + 1] = { sprite = o:getSpriteName(), x = px + dx, y = py + dy, z = pz, index = j }
            end
        end
    end
end end
return out
""") or []

hits, first = 0, None
for o in objects:
    expr = "PZLab.square(%d,%d,%d):getObjects():get(%d)" % (o["x"], o["y"], o["z"], o["index"])
    point = lab.hover(expr)
    hits += point is not None
    if point and not first:
        first = (expr, point)
    print(json.dumps({"sprite": o["sprite"], "at": [o["x"], o["y"], o["z"]], "hover": point}))
lab.input.move(5, 5)
print("hovered %d/%d" % (hits, len(objects)))
check("some container nearby hovered", first is not None, "%d/%d" % (hits, len(objects)))

if first:
    expr, p = first
    x, y = p["x"], p["y"]
    # A 24 x 24 click-through ISPanel centred on the point the container was hovered at.
    lab.lua("""local panel = ISPanel:new(%d, %d, 24, 24)
        panel:initialise() panel:setWantMouseEvents(false) panel:addToUIManager()
        PZLab.probeClickThrough = panel return true""" % (x - 12, y - 12))
    try:
        time.sleep(0.2)  # UIManager adds are applied on the next frame
        lab.input.move(x, y)
        time.sleep(0.2)
        r = lab.lua("""local target = %s
            local s = PZLab.uiState()
            return { picked = UIManager.getLastPicked() ~= nil, uiAt = PZLab.uiAt(), blocked = PZLab.pickBlockedBy(),
                     mouseOverUI = s.mouseOverUI, stateBlocked = s.pickBlockedBy,
                     hovered = PZLab.isHovered(target), hoveredAllowUI = PZLab.isHovered(target, nil, nil, true) }""" % expr)
        check("engine: no world pick under a click-through panel", r["picked"] is False, r)
        check("uiAt ignores the click-through panel (a click there reaches the world)",
              r["uiAt"] is False and r["mouseOverUI"] is False, (r["uiAt"], r["mouseOverUI"]))
        blocked = r["blocked"] or {}
        check("pickBlockedBy reports it as click-through, and uiState().pickBlockedBy at the mouse",
              blocked.get("type") == "ISPanel" and blocked.get("clickThrough") is True
              and (r["stateBlocked"] or {}).get("handle") == blocked.get("handle"), (blocked, r["stateBlocked"]))
        check("isHovered false under it, also with allowUI (getLastPicked is nil there)",
              r["hovered"] is False and r["hoveredAllowUI"] is False)
        aims = lab.lua("return PZLab.aimPoints(%s, 12)" % expr) or []
        marked = [a for a in aims if a.get("ui") == "ISPanel"]
        clear_at = [i for i, a in enumerate(aims) if not a.get("ui")]
        covered_at = [i for i, a in enumerate(aims) if a.get("ui")]
        check("aimPoints marks the points under it (ui, clickThrough), after the clear ones",
              marked and all(a.get("clickThrough") is True for a in marked)
              and (not clear_at or max(clear_at) < min(covered_at)), marked[:3])
        point = lab.hover(expr)
        report = lab.last_hover
        under = [c for c in report["covered"] if c["ui"] == "ISPanel"]
        check("hover skips the covered points and reports them with clickThrough",
              under and all(c["clickThrough"] is True for c in under), report)
        check("hover still finds a point beside the panel",
              point is not None and not lab.lua("return PZLab.pickBlockedBy(%d, %d)" % (point["x"], point["y"])), point)
    finally:
        lab.lua("if PZLab.probeClickThrough then PZLab.probeClickThrough:removeFromUIManager() end "
                "PZLab.probeClickThrough = nil return true")
    time.sleep(0.2)
    lab.input.move(x, y)
    time.sleep(0.2)
    check("panel removed: the point picks the container again", lab.lua("return PZLab.isHovered(%s)" % expr))

    # The click-through panel again, with a 24 x 24 child over the point that consumes mouse events
    # or not. Picking ignores children (blocked either way); a press goes to a consuming child.
    # Left clicks on the container: OnObjectLeftMouseButtonDown counts those reaching the world.
    lab.lua("""PZLabProbeAim = { world = 0, handlers = {} }
        PZLabProbeAim.onDown = function() PZLabProbeAim.world = PZLabProbeAim.world + 1 end
        Events.OnObjectLeftMouseButtonDown.Add(PZLabProbeAim.onDown) return true""")
    try:
        for child_consumes in (False, True):
            lab.lua("""local parent = ISPanel:new(%d, %d, 24, 24)
                parent:initialise() parent:setWantMouseEvents(false)
                local child = ISPanel:new(0, 0, 24, 24)
                child:initialise() child:setWantMouseEvents(%s)
                for name, el in pairs({ parent = parent, child = child }) do
                    local orig = el.onMouseDown
                    el.onMouseDown = function(self, mx, my)
                        PZLabProbeAim.handlers[#PZLabProbeAim.handlers + 1] = name
                        return orig(self, mx, my)
                    end
                end
                parent:addChild(child) parent:addToUIManager()
                PZLab.probeClickThrough = parent return true""" % (x - 12, y - 12, "true" if child_consumes else "false"))
            try:
                time.sleep(0.2)
                lab.input.move(x, y)
                time.sleep(0.2)
                r = lab.lua("return { picked = UIManager.getLastPicked() ~= nil, uiAt = PZLab.uiAt(), "
                            "blocked = PZLab.pickBlockedBy() }")
                lab.lua("PZLabProbeAim.world = 0 PZLabProbeAim.handlers = {} return true")
                lab.input.click(x, y)
                time.sleep(0.4)
                got = lab.lua("return { world = PZLabProbeAim.world, handlers = PZLabProbeAim.handlers }")
            finally:
                lab.lua("PZLab.probeClickThrough:removeFromUIManager() PZLab.probeClickThrough = nil return true")
            what = "consuming" if child_consumes else "click-through"
            blocked, at, handlers = r["blocked"] or {}, r["uiAt"], got["handlers"] or []
            check("%s child: no world pick, pickBlockedBy names the panel (clickThrough %s)" % (what, not child_consumes),
                  r["picked"] is False and blocked.get("type") == "ISPanel"
                  and blocked.get("clickThrough") is (not child_consumes), r)
            if child_consumes:
                check("consuming child: uiAt names the panel and the child; the click stays in the UI",
                      at and at.get("child", {}).get("type") == "ISPanel" and got["world"] == 0
                      and handlers == ["child"], (at, got))
            else:
                check("click-through child: uiAt false; both onMouseDown handlers run and the click reaches the world",
                      at is False and got["world"] == 1 and sorted(handlers) == ["child", "parent"], (at, got))
    finally:
        lab.lua("Events.OnObjectLeftMouseButtonDown.Remove(PZLabProbeAim.onDown) PZLabProbeAim = nil return true")
    lab.input.move(5, 5)

print("%d/%d passed" % (sum(results), len(results)))
