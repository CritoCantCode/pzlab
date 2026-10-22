"""Checks Lab.hover against the engine's own picking for every container object near the
player. Run: python pzlab.py run scripts/probe_aim.py"""

import json

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
""")

hits = 0
for o in objects:
    expr = "PZLab.square(%d,%d,%d):getObjects():get(%d)" % (o["x"], o["y"], o["z"], o["index"])
    point = lab.hover(expr)
    hits += point is not None
    print(json.dumps({"sprite": o["sprite"], "at": [o["x"], o["y"], o["z"]], "hover": point}))
lab.input.move(5, 5)
print("hovered %d/%d" % (hits, len(objects)))
