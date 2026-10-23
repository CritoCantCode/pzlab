-- PZLab fixtures: lab-only world setup a test can create, refer back to by handle and remove
-- again. Items, floor items, world containers, corpses and vehicles made here are tracked;
-- clearFixtures() removes whatever of them still exists, wherever it has moved to. Creation is
-- direct (AddItem, AddWorldInventoryItem, ...) because this is test setup, not gameplay; mods
-- under test must still move items only through vanilla timed actions.
--
-- Shared: in single player and on a dedicated (lab) server. On a server every creation and
-- removal is also sent to the clients (sendAddItemToContainer, transmitCompleteItemToClients,
-- ...). On a multiplayer client they refuse to run (MP_CLIENT_ERROR): objects made there exist
-- only in that client, the server rejects any action on them. Set PZLab.allowClientFixtures =
-- true to make such client-only objects anyway.
--
-- Item spec (addItems, contents options): a list of entries, each
--   "Base.Apple"                         one item
--   { "Base.Apple", 3 }                  three
--   { "Base.Bag_Schoolbag", 1, contents = { ...spec } }   container items, filled recursively

require "PZLab/PZLab_Core"
require "PZLab/PZLab_Inspect"

PZLab.fixtureList = PZLab.fixtureList or {}

local MP_CLIENT_ERROR = "fixtures made on a multiplayer client exist only in that client (the server "
    .. "rejects actions on them); create them on the server: lab.server.lua(...) / pzlab.py server lua"

-- Fixture creation on an MP client: error unless PZLab.allowClientFixtures.
local function checkSide()
    if isClient() and not PZLab.allowClientFixtures then error(MP_CLIENT_ERROR, 3) end
end

-- On a server, tells the clients about an item added to a container (with its contents).
local function syncAdd(container, item)
    if PZLab.onServer then sendAddItemToContainer(container, item) end
end

-- Fixture entries: { kind, obj, id (items), at = {x, y, z} (where it was placed) }. Objects,
-- corpses and vehicles also get modData.pzlabFixture: when their chunk unloads and loads again
-- the world holds new Java objects (items keep their ids), which clearFixtures finds again.
local function track(kind, obj)
    local list = PZLab.fixtureList
    local entry = { kind = kind, obj = obj }
    if kind == "item" then
        entry.id = obj:getID()
    else
        obj:getModData().pzlabFixture = true
        local sq = obj:getSquare()
        if sq then entry.at = { sq:getX(), sq:getY(), sq:getZ() } end
    end
    list[#list + 1] = entry
    PZLab.handle(obj)
    return obj
end

local itemPlace

-- Records where the items tracked since list index `mark` were placed (see clearFixtures).
local function notePlaces(mark)
    local list = PZLab.fixtureList
    for i = mark + 1, #list do
        local f = list[i]
        if f.kind == "item" and not f.at then
            local place = itemPlace(f.obj)
            if place and place ~= "character" and place ~= "unloaded" then
                f.at = { place:getX(), place:getY(), place:getZ() }
            end
        end
    end
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

local addSpec

-- A new tracked item with its contents (spec) already inside, not yet in any container.
function PZLab.newItem(fullType, contents)
    local item = instanceItem(fullType)
    if not item then error("could not create item '" .. tostring(fullType) .. "'") end
    track("item", item)
    if contents then
        if not instanceof(item, "InventoryContainer") then
            error(tostring(fullType) .. " is not a container item; it cannot take contents")
        end
        addSpec(item:getInventory(), contents, nil, false)
    end
    return item
end

-- sync: send each top-level item to the clients (server only; nested contents travel with it).
addSpec = function(container, spec, created, sync)
    for _, entry in ipairs(spec) do
        local fullType, count, contents
        if type(entry) == "string" then
            fullType, count = entry, 1
        else
            fullType, count, contents = entry[1] or entry.type, entry[2] or entry.count or 1, entry.contents
        end
        for _ = 1, count do
            local item = PZLab.newItem(fullType, contents)
            container:AddItem(item)
            if sync then syncAdd(container, item) end
            if created then created[#created + 1] = item end
        end
    end
    container:setDrawDirty(true)
    return created
end

-- Creates the spec's items in `container` (see PZLab.toContainer for accepted targets).
-- Returns the created top-level InventoryItems (each has a handle).
function PZLab.addItems(container, spec)
    checkSide()
    local c = PZLab.toContainer(container)
    if not c then error("addItems: not a container: " .. tostring(container)) end
    local mark = #PZLab.fixtureList
    local created = addSpec(c, spec or {}, {}, true)
    notePlaces(mark)
    return created
end

-- Puts an item on the floor of a square. opts: offX, offY (0..1 within the tile, default
-- 0.5), offZ (height, default 0), contents (spec, container items only).
-- Returns the IsoWorldInventoryObject.
function PZLab.spawnWorldItem(fullType, x, y, z, opts)
    checkSide()
    opts = opts or {}
    local sq = squareOrError(x, y, z)
    local mark = #PZLab.fixtureList
    local item = PZLab.newItem(fullType, opts.contents)
    -- on a server AddWorldInventoryItem also sends the item (with its contents) to the clients
    sq:AddWorldInventoryItem(item, opts.offX or 0.5, opts.offY or 0.5, opts.offZ or 0)
    local worldItem = item:getWorldItem()
    if not worldItem then error("could not place '" .. tostring(fullType) .. "' on the floor") end
    notePlaces(mark)
    PZLab.handle(worldItem)
    return worldItem
end

-- A dead body on the square (vanilla createRandomDeadBody: random outfit and direction).
-- opts: female (true/false; default random), clothed (keep the random outfit; default false,
-- so the corpse container holds exactly `contents`), contents (spec), blood (splats, default 0).
-- Returns the IsoDeadBody; its container is body:getContainer() (type inventorymale/female).
function PZLab.spawnCorpse(x, y, z, opts)
    checkSide()
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
    local mark = #PZLab.fixtureList
    if opts.contents then addSpec(container, opts.contents, nil, false) end
    notePlaces(mark)
    -- a server sends the finished corpse (with its container) to the clients (sendCorpse)
    if PZLab.onServer then sendCorpse(body) end
    return body
end

-- Whether a vehicle really is in the world: its physics transform puts it where its x/y say (a
-- vehicle vanilla refused to place keeps the transform at 0,0; see spawnVehicle).
function PZLab.vehiclePlaced(vehicle)
    if type(vehicle) == "number" then vehicle = PZLab.get(vehicle) end
    if not vehicle or vehicle:isRemovedFromWorld() or not vehicle:getSquare() then return false end
    local w = vehicle:getWorldPos(0, 0, 0, Vector3f.new())
    return math.abs(w:x() - vehicle:getX()) < 2 and math.abs(w:y() - vehicle:getY()) < 2
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
                                if PZLab.onServer then sendRemoveItemFromContainer(container, items:get(k)) end
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
-- Raises when vanilla doesn't place it: addVehicleDebug checks the spot
-- (IsoChunk.doSpawnedVehiclesInInvalidPosition: indoors without a vehicle zone, or overlapping
-- another vehicle) and otherwise still returns a vehicle, which never enters the world (42.21,
-- verified): its physics transform stays at 0,0 (getAreaCenter and getWorldPos near the map
-- origin), on a server its id stays -1 and clients never get it. PZLab.vehiclePlaced tells.
function PZLab.spawnVehicle(scriptName, x, y, z, dir, opts)
    checkSide()
    opts = opts or {}
    local sq = squareOrError(x, y, z)
    if type(dir) == "string" then
        local name = DIRECTIONS[string.upper(dir)]
        if not name then error("unknown direction '" .. dir .. "'") end
        dir = IsoDirections[name]
    end
    local vehicle = addVehicleDebug(scriptName or "Base.CarNormal", dir or IsoDirections.E, nil, sq)
    if not vehicle then error("could not spawn vehicle '" .. tostring(scriptName) .. "'") end
    if not PZLab.vehiclePlaced(vehicle) then
        error(string.format("vanilla refused to place '%s' at %s,%s,%s (indoors, or overlapping another "
            .. "vehicle); use open ground", tostring(scriptName or "Base.CarNormal"), x, y, z or 0))
    end
    track("vehicle", vehicle)
    local mark = #PZLab.fixtureList
    removeStrayKeys(vehicle, 22)
    for i = 0, vehicle:getPartCount() - 1 do
        local part = vehicle:getPartByIndex(i)
        local container = part:getItemContainer()
        if container then
            if not opts.loot then container:removeAllItems() end
            local spec = opts.contents and opts.contents[part:getId()]
            if spec then addSpec(container, spec, nil, true) end
        end
    end
    notePlaces(mark)
    if opts.open then PZLab.openVehicleDoors(vehicle, true) end
    if opts.key then
        local key = vehicle:createVehicleKey()
        local player = PZLab.player(type(opts.key) == "string" and opts.key or nil)
        if key and player then
            player:getInventory():AddItem(key)
            syncAdd(player:getInventory(), key)
            track("item", key)
        end
    end
    return vehicle
end

-- A world container: a new tile object from a sprite whose properties define a container (default
-- "carpentry_01_19", the wooden crate players build), on the square's floor. opts: contents
-- (spec), name (object name). On a server it is sent to the clients with its contents
-- (transmitCompleteItemToClients, then its contents). Returns the IsoObject (obj:getContainer()).
function PZLab.spawnContainer(sprite, x, y, z, opts)
    checkSide()
    opts = opts or {}
    sprite = sprite or "carpentry_01_19"
    local sq = squareOrError(x, y, z)
    if not getSprite(sprite) then error("unknown sprite '" .. tostring(sprite) .. "'") end
    local obj = IsoObject.new(sq, sprite, opts.name, false)
    -- the constructor doesn't make the sprite's container (property "container", e.g. "crate")
    if not obj:getContainer() then obj:createContainersFromSpriteProperties() end
    if not obj:getContainer() then error("sprite '" .. sprite .. "' defines no container") end
    sq:AddTileObject(obj)
    -- Sent empty, then each item on its own: a container sent with items already inside made the
    -- client log "Dupe item ID" for them (42.21).
    if PZLab.onServer then obj:transmitCompleteItemToClients() end
    track("object", obj)
    local mark = #PZLab.fixtureList
    if opts.contents then addSpec(obj:getContainer(), opts.contents, nil, true) end
    notePlaces(mark)
    triggerEvent("OnContainerUpdate")
    return obj
end

-- The vehicle's part containers: { part, index, type, items, area, world = {x, y} (area
-- centre), center = {x, y} (screen), accessible (vehicle:canAccessContainer for the player),
-- handle (of the ItemContainer) }.
function PZLab.vehicleContainers(vehicle)
    if type(vehicle) == "number" then vehicle = PZLab.get(vehicle) end
    if not PZLab.vehiclePlaced(vehicle) then error("the vehicle is not in the world (its area centres would be near 0,0)") end
    local player = PZLab.player()
    local out = {}
    for i = 0, vehicle:getPartCount() - 1 do
        local part = vehicle:getPartByIndex(i)
        local container = part:getItemContainer()
        if container then
            local d = {
                part = part:getId(), index = i, type = container:getType(),
                items = container:getItems():size(), area = part:getArea(),
                accessible = player ~= nil and vehicle:canAccessContainer(i, player),
                handle = PZLab.handle(container),
            }
            local centre = d.area and vehicle:getAreaCenter(d.area)
            if centre then
                d.world = { x = centre:getX(), y = centre:getY() }
                if PZLab.toScreen then d.center = PZLab.toScreen(centre:getX(), centre:getY(), vehicle:getZ()) end
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
        local owner = container:getParent()
        if owner and instanceof(owner, "IsoGameCharacter")
                and (owner:isEquipped(item) or owner:isAttachedItem(item)) then
            owner:removeAttachedItem(item)
            owner:removeFromHands(item)
            owner:removeWornItem(item, false)
            if not PZLab.onServer then triggerEvent("OnClothingUpdated", owner) end
        end
        if PZLab.onServer then sendRemoveItemFromContainer(container, item) end
        container:DoRemoveItem(item)
        container:setDrawDirty(true)
        found = true
    end
    return found
end

local function removeObject(obj)
    local sq = obj:getSquare()
    if not (sq and sq:getObjects():contains(obj)) then return false end
    sq:transmitRemoveItemFromSquare(obj)
    if sq:getObjects():contains(obj) then sq:RemoveTileObject(obj) end
    triggerEvent("OnContainerUpdate")
    return true
end

local function removeFixture(f)
    local obj = f.obj
    if f.kind == "item" then
        return removeItem(obj)
    elseif f.kind == "object" then
        return removeObject(obj)
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

-- Tiles around a connected player within which a server applies (and saves) fixture removals.
PZLab.SERVER_FIXTURE_REACH = PZLab.SERVER_FIXTURE_REACH or 50

-- A square in the live world, where a removal sticks. Client / single player: in the loaded
-- chunks around the player. Server (verified 42.21): getGridSquare still returns squares of cells
-- no player uses any more, but removals made there were lost (the objects came back when a
-- player returned), so it must also be within SERVER_FIXTURE_REACH tiles of a connected player.
local function squareLoaded(sq)
    if sq == nil or PZLab.square(sq:getX(), sq:getY(), sq:getZ()) ~= sq then return false end
    if not PZLab.onServer then return true end
    local reach = PZLab.SERVER_FIXTURE_REACH
    local online = getOnlinePlayers()
    for i = 0, (online and online:size() or 0) - 1 do
        local p = online:get(i)
        if math.abs(p:getX() - sq:getX()) <= reach and math.abs(p:getY() - sq:getY()) <= reach then return true end
    end
    return false
end

-- Where an item is held, for the loaded check: the floor square, the square of what holds its
-- outermost container, or "character" (an inventory). nil when it is nowhere.
itemPlace = function(item)
    local current, guard = item, 0
    while current and guard < 16 do
        guard = guard + 1
        local worldItem = current:getWorldItem()
        if worldItem then return worldItem:getSquare() or "unloaded" end
        local c = current:getContainer()
        if not c then return nil end
        local bag = c:getContainingItem()
        if bag then
            current = bag
        else
            local parent = c:getParent()
            if parent and instanceof(parent, "IsoGameCharacter") then return "character" end
            if parent then return parent:getSquare() or "unloaded" end
            local part = c:getVehiclePart()
            local vehicle = part and part:getVehicle()
            if vehicle then return vehicle:getSquare() or "unloaded" end
            return c:getSourceGrid() or "unloaded"
        end
    end
    return nil
end

-- After its chunk unloaded and loaded again: the world's new object for a fixture, found by
-- item id (around where it was placed, and in players' inventories) or by modData marker on the
-- square it was placed on. Returns true when f.obj was replaced.
local function refresh(f)
    if f.kind == "item" then
        local found = f.at and PZLab.findItem(f.id, { near = f.at, radius = 2, zMin = f.at[3], zMax = f.at[3] })
            or PZLab.findItem(f.id, { radius = 2 })
        if found and found ~= f.obj then
            f.obj = found
            return true
        end
        return false
    end
    if not f.at then return false end
    local sq = PZLab.square(f.at[1], f.at[2], f.at[3])
    if not sq then return false end
    local candidates = {}
    if f.kind == "object" then
        local objects = sq:getObjects()
        for i = 0, objects:size() - 1 do candidates[#candidates + 1] = objects:get(i) end
    elseif f.kind == "corpse" then
        local statics = sq:getStaticMovingObjects()
        for i = 0, statics:size() - 1 do candidates[#candidates + 1] = statics:get(i) end
    elseif f.kind == "vehicle" and sq:getVehicleContainer() then
        candidates[1] = sq:getVehicleContainer()
    end
    for _, obj in ipairs(candidates) do
        if obj ~= f.obj and obj:hasModData() and obj:getModData().pzlabFixture then
            f.obj = obj
            return true
        end
    end
    return false
end

-- The tracked object is in the live world (in a character's inventory counts).
local function liveNow(f)
    local obj = f.obj
    if f.kind == "item" then
        local place = itemPlace(obj)
        if place == "character" then return true end
        return place ~= nil and place ~= "unloaded" and squareLoaded(place)
    end
    if f.kind == "vehicle" and obj:isRemovedFromWorld() then return false end
    local sq = obj:getSquare()
    return sq ~= nil and squareLoaded(sq)
end

-- Whether clearFixtures may deal with a fixture now: its object is in the live world (after a
-- chunk reload: found again, see refresh), or the square it was placed on is live and it is
-- not there any more (gone: moved away or destroyed; nothing to remove). false: its square is
-- not live, a removal now would be lost.
local function reachable(f)
    if liveNow(f) then return true end
    if refresh(f) and liveNow(f) then return true end
    if f.at then
        local sq = PZLab.square(f.at[1], f.at[2], f.at[3])
        return sq ~= nil and squareLoaded(sq)
    end
    return f.kind == "item" and itemPlace(f.obj) == nil
end

-- Removes every tracked fixture that still exists (newest first). Returns the number removed.
-- Fixtures whose square is not loaded (far from the player, or from every connected player on a
-- server) are left alone and stay listed in PZLab.fixtures(): go near them and clear again.
function PZLab.clearFixtures()
    local list = PZLab.fixtureList
    local removed, kept = 0, {}
    for i = #list, 1, -1 do
        local f = list[i]
        if not reachable(f) then
            table.insert(kept, 1, f)
        else
            local ok, done = pcall(removeFixture, f)
            if not ok then
                PZLab.log("clearFixtures: " .. f.kind .. ": " .. tostring(done))
            elseif done then
                removed = removed + 1
            end
        end
        list[i] = nil
    end
    for i, f in ipairs(kept) do list[i] = f end
    if #kept > 0 then PZLab.log("clearFixtures: " .. #kept .. " fixture(s) not removed: their squares are not loaded") end
    return removed
end

-- Tracked fixtures (not yet cleared): { kind, handle, str, loaded (false: its square is not
-- loaded now, clearFixtures skips it) }.
function PZLab.fixtures()
    local out = {}
    for _, f in ipairs(PZLab.fixtureList) do
        out[#out + 1] = { kind = f.kind, handle = PZLab.handle(f.obj), str = tostring(f.obj), loaded = reachable(f) }
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
-- near = {x, y, z} or player = username (server) searches around that point / player instead.
-- Returns describeObject() tables plus handle, dist (tiles), nearest first.
function PZLab.findObjects(filter)
    filter = filter or {}
    local px, py, fz = PZLab.searchCentre(filter)
    if not px then error("findObjects: no player to search around (pass near = {x, y, z})") end
    local pz = math.floor(fz)
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
