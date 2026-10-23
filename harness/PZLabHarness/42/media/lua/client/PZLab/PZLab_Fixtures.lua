-- PZLab fixtures: lab-only world setup a test can create, refer back to by handle and remove
-- again. Items, floor items, corpses and vehicles made here are tracked; clearFixtures()
-- removes whatever of them still exists, wherever it has moved to. Creation is direct
-- (AddItem, AddWorldInventoryItem, ...) because this is test setup, not gameplay; mods under
-- test must still move items only through vanilla timed actions.
--
-- Item spec (addItems, contents options): a list of entries, each
--   "Base.Apple"                         one item
--   { "Base.Apple", 3 }                  three
--   { "Base.Bag_Schoolbag", 1, contents = { ...spec } }   container items, filled recursively

require "PZLab/PZLab_Core"
require "PZLab/PZLab_Tools"

PZLab.fixtureList = PZLab.fixtureList or {}

local function track(kind, obj)
    local list = PZLab.fixtureList
    list[#list + 1] = { kind = kind, obj = obj }
    PZLab.handle(obj)
    return obj
end

local function squareOrError(x, y, z)
    local sq = PZLab.square(x, y, z or 0)
    if not sq then
        error(string.format("no loaded square at %s,%s,%s", tostring(x), tostring(y), tostring(z or 0)))
    end
    return sq
end

-- Anything that holds items -> its ItemContainer: an ItemContainer, a container item, a
-- character (main inventory), a tile object / corpse (first container), a vehicle part, or
-- a handle id of one of those.
function PZLab.toContainer(target)
    if type(target) == "number" then target = PZLab.get(target) end
    if target == nil then return nil end
    if instanceof(target, "ItemContainer") then return target end
    if instanceof(target, "InventoryContainer") then return target:getInventory() end
    if instanceof(target, "IsoGameCharacter") then return target:getInventory() end
    if instanceof(target, "VehiclePart") then return target:getItemContainer() end
    if instanceof(target, "IsoWorldInventoryObject") then return PZLab.toContainer(target:getItem()) end
    if instanceof(target, "IsoObject") then return target:getContainer() end
    return nil
end

local function addSpec(container, spec, created)
    for _, entry in ipairs(spec) do
        local fullType, count, contents
        if type(entry) == "string" then
            fullType, count = entry, 1
        else
            fullType, count, contents = entry[1] or entry.type, entry[2] or entry.count or 1, entry.contents
        end
        for _ = 1, count do
            local item = container:AddItem(fullType)
            if not item then error("could not create item '" .. tostring(fullType) .. "'") end
            track("item", item)
            if contents then
                if not instanceof(item, "InventoryContainer") then
                    error(tostring(fullType) .. " is not a container item; it cannot take contents")
                end
                addSpec(item:getInventory(), contents, nil)
            end
            if created then created[#created + 1] = item end
        end
    end
    container:setDrawDirty(true)
    return created
end

-- Creates the spec's items in `container` (see PZLab.toContainer for accepted targets).
-- Returns the created top-level InventoryItems (each has a handle).
function PZLab.addItems(container, spec)
    local c = PZLab.toContainer(container)
    if not c then error("addItems: not a container: " .. tostring(container)) end
    return addSpec(c, spec or {}, {})
end

-- Puts an item on the floor of a square. opts: offX, offY (0..1 within the tile, default
-- 0.5), offZ (height, default 0), contents (spec, container items only).
-- Returns the IsoWorldInventoryObject.
function PZLab.spawnWorldItem(fullType, x, y, z, opts)
    opts = opts or {}
    local sq = squareOrError(x, y, z)
    local item = sq:AddWorldInventoryItem(fullType, opts.offX or 0.5, opts.offY or 0.5, opts.offZ or 0)
    if not item then error("could not create item '" .. tostring(fullType) .. "'") end
    track("item", item)
    if opts.contents then
        if not instanceof(item, "InventoryContainer") then
            error(tostring(fullType) .. " is not a container item; it cannot take contents")
        end
        addSpec(item:getInventory(), opts.contents, nil)
    end
    local worldItem = item:getWorldItem()
    PZLab.handle(worldItem)
    return worldItem
end

-- A dead body on the square (vanilla createRandomDeadBody: random outfit and direction).
-- opts: female (true/false; default random), clothed (keep the random outfit; default false,
-- so the corpse container holds exactly `contents`), contents (spec), blood (splats, default 0).
-- Returns the IsoDeadBody; its container is body:getContainer() (type inventorymale/female).
function PZLab.spawnCorpse(x, y, z, opts)
    opts = opts or {}
    local sq = squareOrError(x, y, z)
    local body
    for _ = 1, 30 do
        body = createRandomDeadBody(sq, opts.blood or 0)
        if not body then error("createRandomDeadBody returned nil") end
        if opts.female == nil or body:isFemale() == (opts.female == true) then break end
        sq:removeCorpse(body, false)
        body = nil
    end
    if not body then error("could not create a corpse of the requested sex") end
    track("corpse", body)
    local container = body:getContainer()
    if not opts.clothed then
        local items = container:getItems()
        local list = {}
        for i = 0, items:size() - 1 do list[#list + 1] = items:get(i) end
        for _, item in ipairs(list) do
            container:DoRemoveItem(item)
            body:checkClothing(item) -- vanilla keeps worn items and the corpse model in sync this way
        end
        body:invalidateCorpse()
    end
    if opts.contents then addSpec(container, opts.contents, nil) end
    return body
end

local DIRECTIONS = { N = "N", NE = "NE", E = "E", SE = "SE", S = "S", SW = "SW", W = "W", NW = "NW" }

-- Unlocks and opens (open ~= false) or closes (open == false) every door of a vehicle
-- except the hood, as vanilla ISOpenVehicleDoor does. Seat, glove box and trunk containers
-- are only accessible from outside through an open door (Vehicles.ContainerAccess).
-- Returns the number of doors changed.
function PZLab.openVehicleDoors(vehicle, open)
    if type(vehicle) == "number" then vehicle = PZLab.get(vehicle) end
    local changed = 0
    for i = 0, vehicle:getPartCount() - 1 do
        local part = vehicle:getPartByIndex(i)
        local door = part:getDoor()
        if door and part:getId() ~= "EngineDoor" then
            door:setLocked(false)
            door:setOpen(open ~= false)
            vehicle:transmitPartDoor(part)
            changed = changed + 1
        end
    end
    vehicle:setPreviouslyEntered(true) -- opening doors of a never-entered car may set off its alarm
    triggerEvent("OnContainerUpdate")
    return changed
end

-- Vanilla places a new vehicle's key while adding it to the world (BaseVehicle.addToWorld ->
-- trySpawnKey, 42.21): by chance on the floor 10-20 tiles away, in a container nearby, or in
-- the glove box. Removes copies of the vehicle's key from floors and containers within `radius`
-- tiles (not the vehicle's own parts). Returns the number removed.
local function removeStrayKeys(vehicle, radius)
    local keyId = vehicle:getKeyId()
    local cx, cy, z = math.floor(vehicle:getX()), math.floor(vehicle:getY()), math.floor(vehicle:getZ())
    local function isKey(item)
        return item ~= nil and instanceof(item, "Key") and item:getKeyId() == keyId
    end
    local removed = 0
    for dy = -radius, radius do
        for dx = -radius, radius do
            local sq = PZLab.square(cx + dx, cy + dy, z)
            if sq then
                local worldObjects = sq:getWorldObjects()
                for i = worldObjects:size() - 1, 0, -1 do
                    local wo = worldObjects:get(i)
                    if isKey(wo:getItem()) then
                        sq:transmitRemoveItemFromSquare(wo)
                        sq:removeWorldObject(wo)
                        removed = removed + 1
                    end
                end
                local objects = sq:getObjects()
                for i = 0, objects:size() - 1 do
                    local obj = objects:get(i)
                    for c = 0, obj:getContainerCount() - 1 do
                        local container = obj:getContainerByIndex(c)
                        local items = container:getItems()
                        for k = items:size() - 1, 0, -1 do
                            if isKey(items:get(k)) then
                                container:DoRemoveItem(items:get(k))
                                removed = removed + 1
                            end
                        end
                    end
                end
            end
        end
    end
    return removed
end

-- Spawns a vehicle (vanilla addVehicleDebug) centred on the square's north-west corner.
-- dir: "N", "E", ... or an IsoDirections (default "E"). opts: loot (keep the script's random
-- container loot; default false, so part containers hold exactly `contents`), contents =
-- { [partId] = spec } (e.g. { TruckBed = {{"Base.Hammer", 1}} }), open (unlock and open all
-- doors and the trunk, see openVehicleDoors), key (put the vehicle's key in the player's
-- inventory). The key vanilla may drop on the floor or into a container nearby is removed at
-- once (removeStrayKeys), so clearFixtures leaves the area as it was.
function PZLab.spawnVehicle(scriptName, x, y, z, dir, opts)
    opts = opts or {}
    local sq = squareOrError(x, y, z)
    if type(dir) == "string" then
        local name = DIRECTIONS[string.upper(dir)]
        if not name then error("unknown direction '" .. dir .. "'") end
        dir = IsoDirections[name]
    end
    local vehicle = addVehicleDebug(scriptName or "Base.CarNormal", dir or IsoDirections.E, nil, sq)
    if not vehicle then error("could not spawn vehicle '" .. tostring(scriptName) .. "'") end
    track("vehicle", vehicle)
    removeStrayKeys(vehicle, 22)
    for i = 0, vehicle:getPartCount() - 1 do
        local part = vehicle:getPartByIndex(i)
        local container = part:getItemContainer()
        if container then
            if not opts.loot then container:removeAllItems() end
            local spec = opts.contents and opts.contents[part:getId()]
            if spec then addSpec(container, spec, nil) end
        end
    end
    if opts.open then PZLab.openVehicleDoors(vehicle, true) end
    if opts.key then
        local key = vehicle:createVehicleKey()
        if key then
            PZLab.player():getInventory():AddItem(key)
            track("item", key)
        end
    end
    return vehicle
end

-- The vehicle's part containers: { part, index, type, items, area, world = {x, y} (area
-- centre), center = {x, y} (screen), accessible (vehicle:canAccessContainer for the player),
-- handle (of the ItemContainer) }.
function PZLab.vehicleContainers(vehicle)
    if type(vehicle) == "number" then vehicle = PZLab.get(vehicle) end
    local player = PZLab.player()
    local out = {}
    for i = 0, vehicle:getPartCount() - 1 do
        local part = vehicle:getPartByIndex(i)
        local container = part:getItemContainer()
        if container then
            local d = {
                part = part:getId(), index = i, type = container:getType(),
                items = container:getItems():size(), area = part:getArea(),
                accessible = vehicle:canAccessContainer(i, player),
                handle = PZLab.handle(container),
            }
            local centre = d.area and vehicle:getAreaCenter(d.area)
            if centre then
                d.world = { x = centre:getX(), y = centre:getY() }
                d.center = PZLab.toScreen(centre:getX(), centre:getY(), vehicle:getZ())
            end
            out[#out + 1] = d
        end
    end
    return out
end

---------------------------------------------------------------------------------------------
-- Removal
---------------------------------------------------------------------------------------------

-- Removes an item from wherever it is now (floor or any container), the way vanilla's debug
-- Remove Item tool does in single player. Returns true if it was found somewhere.
local function removeItem(item)
    local found = false
    local worldItem = item:getWorldItem()
    if worldItem then
        local sq = worldItem:getSquare()
        if sq then
            sq:transmitRemoveItemFromSquare(worldItem)
            sq:removeWorldObject(worldItem)
        end
        item:setWorldItem(nil)
        found = true
    end
    local container = item:getContainer()
    if container then
        local player = PZLab.player()
        if player and (player:isEquipped(item) or player:isAttachedItem(item)) then
            player:removeAttachedItem(item)
            player:removeFromHands(item)
            player:removeWornItem(item, false)
            triggerEvent("OnClothingUpdated", player)
        end
        container:DoRemoveItem(item)
        container:setDrawDirty(true)
        found = true
    end
    return found
end

local function removeFixture(f)
    local obj = f.obj
    if f.kind == "item" then
        return removeItem(obj)
    elseif f.kind == "corpse" then
        local sq = obj:getSquare()
        if sq and sq:getStaticMovingObjects():contains(obj) then
            sq:removeCorpse(obj, false)
            return true
        end
    elseif f.kind == "vehicle" then
        if not obj:isRemovedFromWorld() then
            obj:permanentlyRemove()
            return true
        end
    end
    return false
end

-- Removes every tracked fixture that still exists (newest first). Returns the number removed.
function PZLab.clearFixtures()
    local list = PZLab.fixtureList
    local removed = 0
    for i = #list, 1, -1 do
        local ok, done = pcall(removeFixture, list[i])
        if not ok then
            PZLab.log("clearFixtures: " .. list[i].kind .. ": " .. tostring(done))
        elseif done then
            removed = removed + 1
        end
        list[i] = nil
    end
    return removed
end

-- Tracked fixtures still alive: { kind, handle, str }.
function PZLab.fixtures()
    local out = {}
    for _, f in ipairs(PZLab.fixtureList) do
        out[#out + 1] = { kind = f.kind, handle = PZLab.handle(f.obj), str = tostring(f.obj) }
    end
    return out
end

---------------------------------------------------------------------------------------------
-- World search
---------------------------------------------------------------------------------------------

local function matches(obj, filter)
    if filter.class and not instanceof(obj, filter.class) then return false end
    if filter.sprite then
        local name = obj:getSpriteName()
        if not (name and string.find(name, filter.sprite)) then return false end
    end
    if filter.hasContainer or filter.containerType then
        local containers = PZLab.containersOf(obj)
        if #containers == 0 then return false end
        if filter.containerType then
            local any = false
            for _, c in ipairs(containers) do
                if c:getType() == filter.containerType then any = true end
            end
            if not any then return false end
        end
    end
    return true
end

-- Objects around the player: tile objects, floor items, corpses and vehicles.
-- filter = { radius = 6, zMin = pz, zMax = pz, hasContainer = true|nil, sprite = "lua pattern",
--            containerType = "crate", class = "IsoDeadBody" (Java class, instanceof), limit = n }
-- Returns describeObject() tables plus handle, dist (tiles), nearest first.
function PZLab.findObjects(filter)
    filter = filter or {}
    local player = PZLab.player()
    local px, py, pz = player:getX(), player:getY(), math.floor(player:getZ())
    local cx, cy = math.floor(px), math.floor(py)
    local radius = filter.radius or 6
    local zMin, zMax = filter.zMin or pz, filter.zMax or pz
    local found, vehicles = {}, {}
    local function consider(obj, ox, oy)
        if matches(obj, filter) then
            local d = PZLab.describeObject(obj)
            d.handle = PZLab.handle(obj)
            d.dist = math.sqrt((ox - px) * (ox - px) + (oy - py) * (oy - py))
            found[#found + 1] = d
        end
    end
    for z = zMin, zMax do
        for dy = -radius, radius do
            for dx = -radius, radius do
                local sq = PZLab.square(cx + dx, cy + dy, z)
                if sq then
                    local sx, sy = sq:getX() + 0.5, sq:getY() + 0.5
                    local objects = sq:getObjects()
                    for i = 0, objects:size() - 1 do
                        local obj = objects:get(i)
                        if instanceof(obj, "IsoWorldInventoryObject") then
                            consider(obj, obj:getWorldPosX(), obj:getWorldPosY())
                        else
                            consider(obj, sx, sy)
                        end
                    end
                    local statics = sq:getStaticMovingObjects()
                    for i = 0, statics:size() - 1 do
                        local obj = statics:get(i)
                        consider(obj, obj:getX(), obj:getY())
                    end
                    local vehicle = sq:getVehicleContainer()
                    if vehicle and not vehicles[vehicle] then
                        vehicles[vehicle] = true
                        consider(vehicle, vehicle:getX(), vehicle:getY())
                    end
                end
            end
        end
    end
    table.sort(found, function(a, b) return a.dist < b.dist end)
    if filter.limit then
        for i = #found, filter.limit + 1, -1 do found[i] = nil end
    end
    return found
end

---------------------------------------------------------------------------------------------
-- Timed actions
---------------------------------------------------------------------------------------------

-- True while the player has timed actions queued or running (walk-to, transfers, ...) or is in
-- an action state (climbing, opening windows); the same test vanilla uses plus the Lua queue.
function PZLab.playerBusy(player)
    player = player or PZLab.player()
    if not player then return false end
    local queue = ISTimedActionQueue.queues[player]
    if queue and #queue.queue > 0 then return true end
    return ISTimedActionQueue.isPlayerDoingAction(player) and true or false
end

-- The player's timed-action queue: { busy, queued = { Type, ... } } (first = running).
function PZLab.actionQueue(player)
    player = player or PZLab.player()
    local queue = ISTimedActionQueue.queues[player]
    local types = {}
    if queue then
        for i, action in ipairs(queue.queue) do types[i] = action.Type or tostring(action) end
    end
    return { busy = PZLab.playerBusy(player), queued = types }
end
