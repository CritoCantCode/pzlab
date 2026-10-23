-- PZLab world helpers: camera zoom, the test-location catalogue and travel to it, and searches
-- for places (buildings anywhere on the map, open ground around the player).

require "PZLab/PZLab_Core"
require "PZLab/PZLab_Bridge"
require "PZLab/PZLab_Tools"

local P = 0
local EPS = 0.0001

---------------------------------------------------------------------------------------------
-- Camera zoom (player 0)
--
-- Core.getZoom(p) is the offscreen buffer's (MultiTextureFBO2) display zoom x tileScale / 2;
-- > 1 shows more world. Core has no setter: Core.doZoomScroll(p, +1 / -1) (the mouse wheel and
-- zoom keys) moves the target one step out / in along the level list, and the camera animates
-- there (~0.2 s, also while paused). Core.getNextZoom(p, 0) returns the current target.
-- Levels: options zoomLevels2x (tile scale 2) / zoomLevels1x, percentages separated by ";";
-- empty = all of Core.getDefaultZoomLevels(); 100 is always included.
---------------------------------------------------------------------------------------------

function PZLab.getZoom()
    return getCore():getZoom(P)
end

-- Supported zoom levels, smallest (closest) first, e.g. { 0.25, 0.5, ..., 2.5 }.
function PZLab.zoomLevels()
    local core = getCore()
    local option = Core.getTileScale() == 2 and core:getOptionZoomLevels2x() or core:getOptionZoomLevels1x()
    local percents = {}
    for s in string.gmatch(option or "", "[^;]+") do
        local n = tonumber(s)
        if n then percents[#percents + 1] = n end
    end
    if #percents == 0 then
        local defaults = core:getDefaultZoomLevels()
        for i = 0, defaults:size() - 1 do percents[#percents + 1] = defaults:get(i) end
    end
    local seen, levels = {}, {}
    percents[#percents + 1] = 100
    for _, p in ipairs(percents) do
        if not seen[p] then
            seen[p] = true
            levels[#levels + 1] = p / 100
        end
    end
    table.sort(levels)
    return levels
end

local function nearestLevel(levels, z)
    local best
    for _, level in ipairs(levels) do
        if best == nil or math.abs(level - z) < math.abs(best - z) then best = level end
    end
    return best
end

-- Snaps z to the nearest supported level, scrolls the camera target there the way the mouse
-- wheel does, and waits until the zoom has settled. A steps job: run it as the chunk's
-- return value (`return PZLab.setZoom(1)`); its result is the final PZLab.getZoom().
function PZLab.setZoom(z, timeoutMs)
    local core = getCore()
    z = tonumber(z)
    if not z then error("setZoom: zoom must be a number") end
    if not core:isZoomEnabled() then error("setZoom: zoom is disabled (options zoom=false)") end
    if core:getAutoZoom(P) then error("setZoom: auto zoom is on for player 1 (options autozoom); it overrides the target") end
    local levels = PZLab.zoomLevels()
    local want = nearestLevel(levels, z)
    for _ = 1, #levels + 1 do
        local target = core:getNextZoom(P, 0)
        if math.abs(target - want) < EPS then break end
        core:doZoomScroll(P, target < want and 1 or -1)
        if math.abs(core:getNextZoom(P, 0) - target) < EPS then
            error("setZoom: the zoom target " .. tostring(target) .. " is not one of the levels " .. table.concat(levels, ", "))
        end
    end
    local last, stable = nil, 0
    return PZLab.steps({
        PZLab.waitUntil(function()
            local current = core:getZoom(P)
            if last ~= nil and math.abs(current - last) < EPS then stable = stable + 1 else stable = 0 end
            last = current
            return stable >= 2 and math.abs(core:getNextZoom(P, 0) - want) < EPS
        end, timeoutMs or 5000),
        function() return core:getZoom(P) end,
    })
end

---------------------------------------------------------------------------------------------
-- Test locations: labkit/locations.json, written to pzlab/locations.txt (a Lua chunk) by the
-- controller at setup. PZLab.locations[name] = { x, y, z, map, verified, about, ... }.
---------------------------------------------------------------------------------------------

function PZLab.loadLocations()
    local src = PZLab.readFile("locations.txt")
    local fn = src and src ~= "" and loadstring(src)
    local ok, data = false, nil
    if fn then ok, data = pcall(fn) end
    PZLab.locations = (ok and type(data) == "table") and data or (PZLab.locations or {})
    return PZLab.locations
end

PZLab.loadLocations()

-- Whether the world around (x, y, z) is loaded: every square within `radius` tiles on the
-- ground floor exists (squares above ground exist only where something is built) and so does
-- the target square.
function PZLab.areaLoaded(x, y, z, radius)
    local cell = getCell()
    if not cell then return false end
    x, y, z, radius = math.floor(x), math.floor(y), math.floor(z or 0), radius or 10
    if not cell:getGridSquare(x, y, z) then return false end
    for dy = -radius, radius do
        for dx = -radius, radius do
            if not cell:getGridSquare(x + dx, y + dy, 0) then return false end
        end
    end
    return true
end

local function resolveTarget(target)
    if type(target) == "string" then
        local loc = PZLab.locations[target] or PZLab.loadLocations()[target]
        if not loc then
            local names = {}
            for name in pairs(PZLab.locations) do names[#names + 1] = name end
            table.sort(names)
            error("unknown location '" .. target .. "' (known: " .. table.concat(names, ", ") .. ")")
        end
        return loc.x, loc.y, loc.z or 0, target
    end
    if type(target) == "table" then
        return target.x or target[1], target.y or target[2], target.z or target[3] or 0, nil
    end
    error("gotoLocation: give a location name or {x, y, z}")
end

-- Teleports player 1 to a catalogue location (name) or {x, y, z} and waits until the area is
-- loaded (PZLab.areaLoaded) and the player stands on the target square, then a few frames for
-- the renderer. opts: radius (10), timeoutMs (30000), settleFrames (10). A steps job: return it
-- from the chunk. Result: { name, x, y, z, player = {x, y, z}, ms }.
function PZLab.gotoLocation(target, opts)
    opts = opts or {}
    local x, y, z, name = resolveTarget(target)
    local radius = opts.radius or 10
    local started
    local function onTarget()
        local sq = PZLab.player():getCurrentSquare()
        return sq ~= nil and sq:getX() == math.floor(x) and sq:getY() == math.floor(y) and sq:getZ() == math.floor(z)
    end
    return PZLab.steps({
        function()
            started = getTimestampMs()
            PZLab.teleport(x, y, z)
        end,
        PZLab.waitUntil(function() return PZLab.areaLoaded(x, y, z, radius) end, opts.timeoutMs or 30000),
        function()
            -- placed again once the squares exist: on an upper floor the first placement can
            -- drop the player before the floor has loaded
            if not onTarget() then PZLab.teleport(x, y, z) end
        end,
        opts.settleFrames or 10,
        PZLab.waitUntil(onTarget, 5000),
        function()
            local p = PZLab.player()
            return { name = name, x = x, y = y, z = z, player = { x = p:getX(), y = p:getY(), z = p:getZ() },
                     ms = getTimestampMs() - started }
        end,
    })
end

---------------------------------------------------------------------------------------------
-- Searching for places
---------------------------------------------------------------------------------------------

local function toSet(list)
    local set = {}
    for _, v in ipairs(list or {}) do set[v] = true end
    return set
end

-- Buildings anywhere on the map, from the world's metagrid (works for unloaded areas too):
-- filter = { near = {x, y} (default: the player), radius = 300, levels = n (at least n floors
--   at z >= 0 with rooms, e.g. 2 for upstairs rooms), rooms = { names all present },
--   anyRooms = { at least one present }, limit = 10 }
-- Room names are the map's (kitchen, bedroom, grocery, gigamart, warehouse, ...).
-- Returns nearest first: { x, y, x2, y2, center = {x, y}, dist, floors = { { z, rooms = {...} } } }.
function PZLab.findBuildings(filter)
    filter = filter or {}
    local near = filter.near
    if not near then
        local p = PZLab.player()
        near = { p:getX(), p:getY() }
    end
    local nx, ny = near.x or near[1], near.y or near[2]
    local radius = filter.radius or 300
    local need, any = toSet(filter.rooms), filter.anyRooms and toSet(filter.anyRooms) or nil
    local out = {}
    local list = getWorld():getMetaGrid():getBuildings()
    for i = 0, list:size() - 1 do
        local b = list:get(i)
        local cx, cy = (b:getX() + b:getX2()) / 2, (b:getY() + b:getY2()) / 2
        local dist = math.sqrt((cx - nx) * (cx - nx) + (cy - ny) * (cy - ny))
        if dist <= radius then
            local rooms = b:getRooms()
            local byZ, present, levels = {}, {}, 0
            for j = 0, rooms:size() - 1 do
                local r = rooms:get(j)
                local z, roomName = r:getZ(), r:getName()
                if not byZ[z] then
                    byZ[z] = {}
                    if z >= 0 then levels = levels + 1 end
                end
                table.insert(byZ[z], roomName)
                present[roomName] = true
            end
            local ok = levels >= (filter.levels or 0)
            for roomName in pairs(need) do ok = ok and present[roomName] == true end
            if ok and any then
                local hit = false
                for roomName in pairs(any) do hit = hit or present[roomName] == true end
                ok = hit
            end
            if ok then
                local floors = {}
                for z, names in pairs(byZ) do floors[#floors + 1] = { z = z, rooms = names } end
                table.sort(floors, function(a, c) return a.z < c.z end)
                out[#out + 1] = { x = b:getX(), y = b:getY(), x2 = b:getX2(), y2 = b:getY2(),
                                  center = { x = math.floor(cx), y = math.floor(cy) }, dist = dist, floors = floors }
            end
        end
    end
    table.sort(out, function(a, c) return a.dist < c.dist end)
    for i = #out, (filter.limit or 10) + 1, -1 do out[i] = nil end
    return out
end

-- Overlays a vehicle can stand on: grass, ground decals, terrain blends.
PZLab.openGroundSprites = PZLab.openGroundSprites or { "^e_newgrass", "^d_generic", "^d_streetcracks", "^blends_" }

local function openGround(sq)
    if not sq or not sq:isOutside() or sq:getBuilding() or not sq:isFree(false) then return false end
    if sq:getVehicleContainer() then return false end
    local objects = sq:getObjects()
    for i = 0, objects:size() - 1 do
        local obj = objects:get(i)
        if not obj:isFloor() then
            local name = obj:getSpriteName()
            local allowed = false
            if name then
                for _, pattern in ipairs(PZLab.openGroundSprites) do
                    if string.find(name, pattern) then allowed = true break end
                end
            end
            if not allowed then return false end
        end
    end
    if sq:getStaticMovingObjects():size() > 0 then return false end
    return true
end

-- Whether a square is open outdoor ground a vehicle can be spawned on (see findOpenArea).
function PZLab.isOpenGround(x, y, z)
    return openGround(PZLab.square(x, y, z or 0))
end

-- The nearest loaded w x h rectangle of open outdoor ground (no buildings, walls, fences,
-- trees, furniture, corpses or vehicles; only floor, grass and ground decals; see
-- PZLab.openGroundSprites) within `radius` tiles of the player on floor z (default 0).
-- opts = { radius = 30, z = 0, near = {x, y} }. Returns { x, y, w, h, center = {x, y}, dist }
-- or nil. The rectangle spans x .. x+w-1, y .. y+h-1.
function PZLab.findOpenArea(w, h, opts)
    opts = opts or {}
    w, h = w or 6, h or 10
    local radius = opts.radius or 30
    local z = opts.z or 0
    local p = PZLab.player()
    local near = opts.near or { p:getX(), p:getY() }
    local nx, ny = near.x or near[1], near.y or near[2]
    local x0, y0 = math.floor(nx) - radius, math.floor(ny) - radius
    local size = 2 * radius + 1
    -- summed-area table of open squares: S[r][c] = open count in rows 1..r, columns 1..c
    local S = {}
    S[0] = {}
    for c = 0, size do S[0][c] = 0 end
    for r = 1, size do
        local row, above = { [0] = 0 }, S[r - 1]
        local acc = 0
        for c = 1, size do
            if openGround(PZLab.square(x0 + c - 1, y0 + r - 1, z)) then acc = acc + 1 end
            row[c] = above[c] + acc
        end
        S[r] = row
    end
    local best
    for r = h, size do
        for c = w, size do
            local count = S[r][c] - S[r - h][c] - S[r][c - w] + S[r - h][c - w]
            if count == w * h then
                local x, y = x0 + c - w, y0 + r - h
                local cx, cy = x + w / 2, y + h / 2
                local dist = math.sqrt((cx - nx) * (cx - nx) + (cy - ny) * (cy - ny))
                if not best or dist < best.dist then
                    best = { x = x, y = y, w = w, h = h, center = { x = math.floor(cx), y = math.floor(cy) }, dist = dist }
                end
            end
        end
    end
    return best
end
