-- PZLab world access and inspection for both sides (game client and dedicated server): the
-- player, squares, objects and their containers, where an item is. Plain tables out, so results
-- serialize through PZLab.json. Screen positions only where a client has a screen.

require "PZLab/PZLab_Core"
require "PZLab/PZLab_Bridge"

local function className(obj)
    local ok, name = pcall(getClassSimpleName, obj)
    return ok and tostring(name) or "?"
end

-- Client: the local player 0. Server: the connected player with that username, else
-- PZLab.serverPlayerName's, else the first connected player (nil when nobody is online).
-- (getPlayerFromUsername returns nil on a dedicated server, 42.21: the online list is searched.)
function PZLab.player(name)
    if not PZLab.onServer then return getSpecificPlayer(0) end
    name = name or PZLab.serverPlayerName
    local online = getOnlinePlayers()
    for i = 0, (online and online:size() or 0) - 1 do
        local p = online:get(i)
        if not name or p:getUsername() == name then return p end
    end
    return nil
end

-- Connected players (server: every online player; client: the local player and the remote
-- players this client knows): { user, x, y, z, local, handle }.
function PZLab.players()
    local out = {}
    if not (PZLab.onServer or isClient()) then
        local p = getSpecificPlayer(0)
        if p then out[1] = { user = p:getUsername(), x = p:getX(), y = p:getY(), z = p:getZ(),
                             ["local"] = true, handle = PZLab.handle(p) } end
        return out
    end
    local list = getOnlinePlayers()
    for i = 0, (list and list:size() or 0) - 1 do
        local p = list:get(i)
        out[#out + 1] = { user = p:getUsername(), x = p:getX(), y = p:getY(), z = p:getZ(),
                          ["local"] = p:isLocalPlayer(), handle = PZLab.handle(p) }
    end
    return out
end

-- Whether (x, y) lies on the authored map: its 8x8 chunk has map data (IsoMetaGrid:getChunkData).
-- The metagrid's bounds are no help (+-250 cells in 42.21) and neither is isValidSquare (true near
-- 0,0). Off the map a player has no square on either side and timed actions never start.
function PZLab.onMap(x, y)
    local world = getWorld()
    local grid = world and world:getMetaGrid()
    if not grid then return true end
    return grid:getChunkData(math.floor(x / 8), math.floor(y / 8)) ~= nil
end

-- A loaded square or nil. On a server only the chunks around connected players are loaded.
function PZLab.square(x, y, z)
    return getCell():getGridSquare(x, y, z or 0)
end

function PZLab.describeContainer(c)
    if not c then return nil end
    local d = { type = c:getType(), items = c:getItems():size() }
    local holder = c:getContainingItem()
    if holder then d.inItem = holder:getFullType() end
    return d
end

-- Item containers an object holds: tile object containers, a corpse's inventory, a vehicle's
-- part containers, or a floor item's own inventory (bags).
function PZLab.containersOf(obj)
    local out = {}
    if instanceof(obj, "BaseVehicle") then
        for i = 0, obj:getPartCount() - 1 do
            local c = obj:getPartByIndex(i):getItemContainer()
            if c then out[#out + 1] = c end
        end
    elseif instanceof(obj, "IsoWorldInventoryObject") then
        local item = obj:getItem()
        if item and instanceof(item, "InventoryContainer") then out[1] = item:getInventory() end
    else
        for i = 0, obj:getContainerCount() - 1 do out[#out + 1] = obj:getContainerByIndex(i) end
        if #out == 0 and instanceof(obj, "IsoDeadBody") and obj:getContainer() then out[1] = obj:getContainer() end
    end
    return out
end

function PZLab.describeObject(obj)
    if not obj then return nil end
    local d = { class = className(obj), str = tostring(obj) }
    local sq = obj:getSquare()
    if sq then d.at = { sq:getX(), sq:getY(), sq:getZ() } end
    d.sprite = obj:getSpriteName()
    d.name = obj:getObjectName()
    local containers = PZLab.containersOf(obj)
    if #containers > 0 then
        d.containers = {}
        for i, c in ipairs(containers) do d.containers[i] = PZLab.describeContainer(c) end
    end
    if instanceof(obj, "IsoWorldInventoryObject") then
        local item = obj:getItem()
        d.item = item and item:getFullType()
        d.itemId = item and item:getID()
    elseif instanceof(obj, "IsoDeadBody") then
        d.female = obj:isFemale()
    elseif instanceof(obj, "BaseVehicle") then
        d.script = obj:getScriptName()
    end
    if instanceof(obj, "IsoMovingObject") then d.pos = { obj:getX(), obj:getY(), obj:getZ() } end
    if PZLab.objectScreenPos then d.screen = PZLab.objectScreenPos(obj) end
    return d
end

-- Everything on a square: tile objects, world items, corpses.
function PZLab.objectsAt(x, y, z)
    local sq = PZLab.square(x, y, z)
    if not sq then return nil end
    local out = {}
    local objects = sq:getObjects()
    for i = 0, objects:size() - 1 do
        out[#out + 1] = PZLab.describeObject(objects:get(i))
    end
    local statics = sq:getStaticMovingObjects()
    for i = 0, statics:size() - 1 do
        out[#out + 1] = PZLab.describeObject(statics:get(i))
    end
    return out
end

-- Centre for searches: opts.near = {x, y, z}, else the player (PZLab.player(opts.player));
-- nil when there is neither.
function PZLab.searchCentre(opts)
    opts = opts or {}
    if opts.near then
        local n = opts.near
        return n.x or n[1], n.y or n[2], n.z or n[3] or 0
    end
    local player = PZLab.player(opts.player)
    if not player then return nil end
    return player:getX(), player:getY(), player:getZ()
end

local function centreOrError(opts)
    local x, y, z = PZLab.searchCentre(opts)
    if not x then error("no player to search around (pass near = {x, y, z})") end
    return x, y, z
end

-- Tile objects with containers within `radius` tiles of the player (same floor), nearest first.
-- opts: near = {x, y, z} / player = username (server) instead of the player.
function PZLab.findContainers(radius, opts)
    local fx, fy, fz = centreOrError(opts)
    local px, py, pz = math.floor(fx), math.floor(fy), math.floor(fz)
    radius = radius or 6
    local out = {}
    for dy = -radius, radius do
        for dx = -radius, radius do
            local sq = PZLab.square(px + dx, py + dy, pz)
            if sq then
                local objects = sq:getObjects()
                for i = 0, objects:size() - 1 do
                    local obj = objects:get(i)
                    if obj:getContainerCount() > 0 then
                        local d = PZLab.describeObject(obj)
                        d.handle = PZLab.handle(obj)
                        d.dist = math.sqrt(dx * dx + dy * dy)
                        out[#out + 1] = d
                    end
                end
            end
        end
    end
    table.sort(out, function(a, b) return a.dist < b.dist end)
    return out
end

-- Lab setup only (untracked; mods under test must use vanilla timed actions).
function PZLab.addToContainer(container, fullType, count)
    for _ = 1, count or 1 do container:AddItem(fullType) end
    return container:getItems():size()
end

---------------------------------------------------------------------------------------------
-- Where an item is
---------------------------------------------------------------------------------------------

-- One step outwards from a container: what holds it.
local function holderOf(c)
    local bag = c:getContainingItem()
    if bag then
        return { kind = "item", type = bag:getFullType(), id = bag:getID(), container = c:getType() }, bag
    end
    local part = c:getVehiclePart()
    if part then
        local v = part:getVehicle()
        return { kind = "vehicle", part = part:getId(), container = c:getType(),
                 vehicle = v and v:getScriptName(), vehicleId = v and v:getId() }, nil
    end
    local parent = c:getParent()
    if parent and instanceof(parent, "IsoGameCharacter") then
        local d = { kind = "character", container = c:getType() }
        if instanceof(parent, "IsoPlayer") then d.user = parent:getUsername() end
        return d, nil
    end
    if parent and instanceof(parent, "IsoDeadBody") then
        local sq = parent:getSquare()
        return { kind = "corpse", container = c:getType(), at = sq and { sq:getX(), sq:getY(), sq:getZ() } }, nil
    end
    if parent then
        local sq = parent:getSquare()
        return { kind = "object", container = c:getType(), sprite = parent:getSpriteName(),
                 index = parent:getObjectIndex(), at = sq and { sq:getX(), sq:getY(), sq:getZ() } }, nil
    end
    local sq = c:getSourceGrid()
    return { kind = "container", container = c:getType(), at = sq and { sq:getX(), sq:getY(), sq:getZ() } }, nil
end

-- Where an item (or handle) is, as this side sees it: { id, type, on = "floor" | "container" |
-- "nowhere", path = { innermost holder first ... outermost last } }. Path entries:
--   { kind = "item", type, id, container }      a container item (bag) holding it
--   { kind = "character", user, container }     a character's inventory
--   { kind = "object", sprite, index, at, container }   a tile object (furniture, crate)
--   { kind = "corpse", at, container }, { kind = "vehicle", part, vehicle, vehicleId, container }
--   { kind = "floor", at, x, y, z }              lying on the floor (a world item)
-- e.g. an apple in a bag in a crate: path = { {kind="item", type="Base.Bag_Schoolbag"},
-- {kind="object", sprite=..., at={x,y,z}} }.
function PZLab.whereIs(item)
    if type(item) == "number" then item = PZLab.get(item) end
    if item == nil then return nil end
    local out = { id = item:getID(), type = item:getFullType(), path = {} }
    local current, guard = item, 0
    while current and guard < 16 do
        guard = guard + 1
        local worldItem = current:getWorldItem()
        if worldItem and worldItem:getSquare() then
            local sq = worldItem:getSquare()
            out.path[#out.path + 1] = { kind = "floor", at = { sq:getX(), sq:getY(), sq:getZ() },
                                        x = worldItem:getWorldPosX(), y = worldItem:getWorldPosY(),
                                        z = worldItem:getWorldPosZ() }
            break
        end
        local c = current:getContainer()
        if not c then break end
        local entry, nextItem = holderOf(c)
        out.path[#out.path + 1] = entry
        current = nextItem
    end
    if #out.path == 0 then
        out.on = "nowhere"
    elseif #out.path == 1 and out.path[1].kind == "floor" then
        out.on = "floor"
    else
        out.on = "container"
    end
    return out
end

-- Searches a container tree for an item id (ItemContainer:getItemWithIDRecursiv).
local function findIn(c, id)
    if not c then return nil end
    return c:getItemWithIDRecursiv(id)
end

-- Finds an item by id (InventoryItem:getID(), the same on server and clients) near a point or
-- player and in every connected player's inventory: tile object containers, floor items (and
-- their contents), corpses and vehicles within opts.radius (default 8) tiles, floors
-- opts.zMin..opts.zMax (default the centre's). opts.near = {x, y, z} / opts.player = username.
-- Returns the item (nil when not found); PZLab.locate(id, opts) returns whereIs() of it.
function PZLab.findItem(id, opts)
    opts = opts or {}
    local list = (PZLab.onServer or isClient()) and getOnlinePlayers() or nil
    for i = 0, (list and list:size() or 0) - 1 do
        local found = findIn(list:get(i):getInventory(), id)
        if found then return found end
    end
    if not PZLab.onServer and getSpecificPlayer(0) then
        local found = findIn(getSpecificPlayer(0):getInventory(), id)
        if found then return found end
    end
    local fx, fy, fz = PZLab.searchCentre(opts)
    if not fx then return nil end
    local cx, cy, cz = math.floor(fx), math.floor(fy), math.floor(fz)
    local radius = opts.radius or 8
    local vehicles = {}
    for z = opts.zMin or cz, opts.zMax or cz do
        for dy = -radius, radius do
            for dx = -radius, radius do
                local sq = PZLab.square(cx + dx, cy + dy, z)
                if sq then
                    local objects = sq:getObjects()
                    for i = 0, objects:size() - 1 do
                        local obj = objects:get(i)
                        if instanceof(obj, "IsoWorldInventoryObject") then
                            local item = obj:getItem()
                            if item and item:getID() == id then return item end
                            if item and instanceof(item, "InventoryContainer") then
                                local found = findIn(item:getInventory(), id)
                                if found then return found end
                            end
                        else
                            for k = 0, obj:getContainerCount() - 1 do
                                local found = findIn(obj:getContainerByIndex(k), id)
                                if found then return found end
                            end
                        end
                    end
                    local statics = sq:getStaticMovingObjects()
                    for i = 0, statics:size() - 1 do
                        local obj = statics:get(i)
                        if instanceof(obj, "IsoDeadBody") then
                            local found = findIn(obj:getContainer(), id)
                            if found then return found end
                        end
                    end
                    local vehicle = sq:getVehicleContainer()
                    if vehicle and not vehicles[vehicle] then
                        vehicles[vehicle] = true
                        for p = 0, vehicle:getPartCount() - 1 do
                            local found = findIn(vehicle:getPartByIndex(p):getItemContainer(), id)
                            if found then return found end
                        end
                    end
                end
            end
        end
    end
    return nil
end

-- whereIs() of the item with that id, or { id, on = "nowhere" } when PZLab.findItem finds none.
function PZLab.locate(id, opts)
    local item = PZLab.findItem(id, opts)
    if not item then return { id = id, on = "nowhere", path = {} } end
    local where = PZLab.whereIs(item)
    where.handle = PZLab.handle(item)
    return where
end

-- whereIs() as one line: "item:Base.Bag_Schoolbag < object:carpentry_01_19" (innermost holder
-- first), "floor", "character:admin", or "nowhere".
function PZLab.placeText(where)
    if not where or not where.path or #where.path == 0 then return "nowhere" end
    local parts = {}
    for i, p in ipairs(where.path) do
        local label = p.type or p.sprite or p.user or p.part
        parts[i] = label and (p.kind .. ":" .. tostring(label)) or p.kind
    end
    return table.concat(parts, " < ")
end

-- Item watch: from now on, every tick, where each item is (PZLab.placeText). Changes are
-- recorded with getTimestampMs() (System.currentTimeMillis: the same clock in a server and a
-- client on one machine, so their times compare). ids: item ids; opts as PZLab.locate (near,
-- player, radius) for finding them again. Returns the initial places. PZLab.itemWatch(stop) ->
-- { [id] = { place, changes = { { t, from, to } } } }; stop = true ends the watch.
function PZLab.watchItems(ids, opts)
    local watch = { opts = opts or {}, items = {} }
    local initial = {}
    for _, id in ipairs(ids) do
        local item = PZLab.findItem(id, watch.opts)
        local place = item and PZLab.placeText(PZLab.whereIs(item)) or "nowhere"
        watch.items[id] = { obj = item, place = place, changes = {} }
        initial[tostring(id)] = place
    end
    PZLab.activeItemWatch = watch
    PZLab.tickHooks.itemWatch = function()
        local w = PZLab.activeItemWatch
        if not w then return end
        for id, e in pairs(w.items) do
            local where = e.obj and PZLab.whereIs(e.obj)
            if not where or where.on == "nowhere" then
                e.obj = PZLab.findItem(id, w.opts)
                where = e.obj and PZLab.whereIs(e.obj)
            end
            local place = where and PZLab.placeText(where) or "nowhere"
            if place ~= e.place then
                e.changes[#e.changes + 1] = { t = getTimestampMs(), from = e.place, to = place }
                e.place = place
            end
        end
    end
    return initial
end

function PZLab.itemWatch(stop)
    local w = PZLab.activeItemWatch
    local out = {}
    if w then
        for id, e in pairs(w.items) do out[tostring(id)] = { place = e.place, changes = e.changes } end
    end
    if stop then
        PZLab.activeItemWatch = nil
        PZLab.tickHooks.itemWatch = nil
    end
    return out
end
