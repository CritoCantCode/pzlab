-- PZLab fixtures: lab-only world setup a test can create, refer back to by handle and remove
-- again. Items, floor items, world containers, tile objects (any sprite), B42 entities, corpses
-- and vehicles made here are tracked; clearFixtures() removes whatever of them still exists,
-- wherever it has moved to. Creation is direct (AddItem, AddWorldInventoryItem, vanilla's map
-- loader and build code, ...) because this is test setup, not gameplay; mods under test must
-- still move items only through vanilla timed actions.
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

-- Fixture entries: { kind, obj, id (items), mark, at = {x, y, z} (where it was placed) }. Objects,
-- corpses and vehicles also get modData.pzlabFixture = mark (unique per fixture): when their chunk
-- unloads and loads again the world holds new Java objects (items keep their ids), which
-- clearFixtures finds again by that mark, also when several fixtures share a square.
local function track(kind, obj)
    local list = PZLab.fixtureList
    local entry = { kind = kind, obj = obj }
    if kind == "item" then
        entry.id = obj:getID()
    else
        PZLab.fixtureSeq = (PZLab.fixtureSeq or 0) + 1
        entry.mark = PZLab.fixtureSeq
        obj:getModData().pzlabFixture = entry.mark
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

-- The sprite of that name, or an error. getSprite never returns nil: an unknown name gives one
-- shared empty sprite whose getName() is nil (42.21).
local function spriteOrError(name)
    local sprite = type(name) == "string" and getSprite(name) or nil
    if not sprite or sprite:getName() ~= name then error("unknown sprite '" .. tostring(name) .. "'") end
    return sprite
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
    spriteOrError(sprite)
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

---------------------------------------------------------------------------------------------
-- World objects from tile sprites, and B42 entities
---------------------------------------------------------------------------------------------

local FACES = { "N", "W", "S", "E" }

-- The square's objects as a set (floor items included, so they are never taken for new objects).
local function objectSet(sq)
    local set, objects = {}, sq:getObjects()
    for i = 0, objects:size() - 1 do set[objects:get(i)] = true end
    return set
end

-- Objects on the square that weren't in `before`.
local function newObjects(sq, before)
    local found, objects = {}, sq:getObjects()
    for i = 0, objects:size() - 1 do
        local obj = objects:get(i)
        if not before[obj] then found[#found + 1] = obj end
    end
    return found
end

-- A tile object made the way a loading map makes it. Vanilla createTile(sprite, square) runs the
-- map loader's factory (CellLoader.DoTileObjectCreation: the Java class for the sprite, e.g.
-- IsoStove, IsoLightSwitch, IsoRadio, IsoTelevision, IsoMannequin, else IsoObject; containers from
-- the sprite properties; entity components of workstation sprites) and puts the object straight
-- into the square's lists, as a chunk being loaded does, without adding it to the live world (a
-- mannequin then throws on every frame it renders). So each new object is taken out of the lists
-- again and added the way runtime additions are (AddSpecialObject / AddTileObject: addToWorld,
-- recalculation, collision, pathfinding, lighting, rendering). Returns the new objects; none when
-- the sprite was merged into an existing object (an overlay).
local function placeAsMap(sq, spriteName)
    local before = objectSet(sq)
    createTile(spriteName, sq)
    local made = newObjects(sq, before)
    local objects, specials = sq:getObjects(), sq:getSpecialObjects()
    for _, obj in ipairs(made) do
        local special = specials:contains(obj)
        objects:remove(obj)
        if special then
            specials:remove(obj)
            sq:AddSpecialObject(obj)
        else
            sq:AddTileObject(obj)
        end
    end
    return made
end

-- A tile object placed the way a player places a moveable: vanilla
-- ISMoveableSpriteProps:placeMoveableInternal with a plank as the carried item, as the debug brush
-- tool (ISBrushToolTileCursor) and prop entities (ISBuildIsoEntity:setInfo) do. Solid furniture
-- becomes an IsoThumpable; containers are explored; OnObjectAdded fires; a server sends it to the
-- clients; grass on the square is removed. Returns the new objects.
local function placeAsMoveable(sq, spriteName, character)
    local before = objectSet(sq)
    local props = ISMoveableSpriteProps.new(spriteName)
    props.rawWeight = 10
    props:placeMoveableInternal(character, sq, instanceItem("Base.Plank"), spriteName)
    return newObjects(sq, before)
end

-- Fills the containers of a fixture's objects: list entries (spec) go into the first container,
-- [containerType] = spec into the first container of that type (e.g. { fridge = {...}, freezer = {...} }).
local function fillContents(parts, contents)
    if not contents then return end
    local list, byType = {}, {}
    for k, v in pairs(contents) do
        if type(k) == "number" then list[k] = v else byType[k] = v end
    end
    local function find(containerType)
        for _, obj in ipairs(parts) do
            for c = 0, obj:getContainerCount() - 1 do
                local container = obj:getContainerByIndex(c)
                if containerType == nil or container:getType() == containerType then return container end
            end
        end
        error(containerType and ("no '" .. tostring(containerType) .. "' container to put contents in")
            or "the object has no container to put contents in")
    end
    if #list > 0 then addSpec(find(nil), list, nil, true) end
    for containerType, spec in pairs(byType) do addSpec(find(containerType), spec, nil, true) end
end

-- After placing (every part already tracked): containers explored, so they hold exactly
-- `contents` (no loot); objects in `transmit` sent to the clients on a server (placeAsMap's; the
-- moveable and build paths send their own); then the contents, item by item (see spawnContainer).
local function finishObjects(parts, transmit, contents)
    local hasContainer = false
    for _, obj in ipairs(parts) do
        for c = 0, obj:getContainerCount() - 1 do
            obj:getContainerByIndex(c):setExplored(true)
            hasContainer = true
        end
        if transmit[obj] and PZLab.onServer then obj:transmitCompleteItemToClients() end
    end
    local mark = #PZLab.fixtureList
    fillContents(parts, contents)
    notePlaces(mark)
    if hasContainer then triggerEvent("OnContainerUpdate") end
end

-- The moveable sprite for another facing (vanilla ISMoveableSpriteProps:getFaces).
local function faceSprite(spriteName, face)
    local faces = ISMoveableSpriteProps.new(spriteName):getFaces()
    local name = faces[string.upper(tostring(face))]
    if not name then
        local have = {}
        for _, f in ipairs(FACES) do
            if faces[f] then have[#have + 1] = f end
        end
        error(string.format("sprite '%s' has no face '%s' (faces: %s)", spriteName, tostring(face),
            #have > 0 and table.concat(have, ", ") or "none, it is not a rotatable moveable"))
    end
    return name
end

-- A world object from any tile sprite: furniture, appliances, fixtures, lamps, ... By default as
-- the map loader makes it (placeAsMap: the class and containers a map-loaded one has); with
-- opts.placed as a player places the moveable (placeAsMoveable: solid furniture is an
-- IsoThumpable). opts: placed, whole (a multi-tile sprite such as a two-tile table or a bed: every
-- sprite of its grid at its offset, this sprite on x, y), face ("N", "W", "S", "E": the moveable's
-- sprite for that facing instead), contents (spec for the first container and/or
-- [containerType] = spec). Containers hold exactly `contents` (a mannequin's also the outfit it
-- wears). The square is not checked for room. On a server the objects reach the clients.
-- Returns the object of `sprite` and the list of all objects made (that one first).
function PZLab.spawnObject(sprite, x, y, z, opts)
    checkSide()
    opts = opts or {}
    z = z or 0
    spriteOrError(sprite)
    if opts.face then sprite = faceSprite(sprite, opts.face) end
    local s = spriteOrError(sprite)
    if s:getProperties():has(IsoFlagType.solidfloor) then
        error("'" .. sprite .. "' is a floor tile, not an object (the map loader would replace the square's floor)")
    end
    local tiles = { { square = squareOrError(x, y, z), sprite = sprite } }
    local grid = opts.whole and s:getSpriteGrid()
    if grid then
        local ax, ay = x - grid:getSpriteGridPosX(s), y - grid:getSpriteGridPosY(s)
        for gy = 0, grid:getHeight() - 1 do
            for gx = 0, grid:getWidth() - 1 do
                local member = grid:getSprite(gx, gy)
                if member and member:getName() ~= sprite then
                    tiles[#tiles + 1] = { square = squareOrError(ax + gx, ay + gy, z), sprite = member:getName() }
                end
            end
        end
    end
    local character = PZLab.player()
    local parts, transmit = {}, {}
    for _, tile in ipairs(tiles) do
        local made
        if opts.placed then
            made = placeAsMoveable(tile.square, tile.sprite, character)
        else
            made = placeAsMap(tile.square, tile.sprite)
        end
        for _, obj in ipairs(made) do
            track("object", obj)
            parts[#parts + 1] = obj
            if not opts.placed then transmit[obj] = true end
        end
        if #made == 0 then
            error(string.format("no new object for sprite '%s' at %d,%d,%d: it was merged into an existing object (an overlay)",
                tile.sprite, tile.square:getX(), tile.square:getY(), tile.square:getZ()))
        end
    end
    finishObjects(parts, transmit, opts.contents)
    return parts[1], parts
end

-- ISBuildIsoEntity.nSprite (the build cursor's rotation, 1-4) of each facing.
local ENTITY_FACE_INDEX = { W = 1, N = 2, E = 3, S = 4 }

-- An entity script's SpriteConfigManager object info, found as the debug Entity scripts panel does.
local function entityInfo(scriptName)
    local script = type(scriptName) == "string" and ScriptManager.instance:getGameEntityScript(scriptName) or nil
    if not script then error("unknown entity script '" .. tostring(scriptName) .. "'") end
    local config = script:getComponentScriptFor(ComponentType.SpriteConfig)
    if not config then error("entity '" .. scriptName .. "' has no SpriteConfig component: it has no world object") end
    local infos = SpriteConfigManager.GetObjectInfoList()
    for i = 0, infos:size() - 1 do
        if infos:get(i):getScript() == config then return infos:get(i) end
    end
    error("no sprite configuration loaded for entity '" .. scriptName .. "'")
end

-- A B42 entity (workstation, furniture, ...) from its entity script, built as the build menu
-- builds it: vanilla ISBuildIsoEntity (the build cursor's object), its validity check (isValid:
-- free squares, a floor, no wall between its squares, the script's OnIsValid; materials are not
-- needed) and its per-square placement (setInfo: an IsoThumpable per square, health from the
-- builder's skills, components from the script via GameEntityFactory.CreateIsoObjectEntity,
-- multi-square master and slaves, the script's OnCreate, sent to the clients on a server). Nothing
-- is consumed and no craft history is recorded. Entities without a build recipe (world-only ones,
-- e.g. Base.WaterDispenser) are placed as the map loader places their sprites (placeAsMap).
-- opts: face ("N", "W", "S", "E"; default the build cursor's first: W, else N, E, S), force (skip
-- the validity check), player (server: the builder's username; default PZLab.player()), contents
-- (as spawnObject). Returns the master object (it carries CraftBench and the other components) and
-- the list of all its objects.
function PZLab.spawnEntity(scriptName, x, y, z, opts)
    checkSide()
    opts = opts or {}
    z = z or 0
    local info = entityInfo(scriptName)
    local character = PZLab.player(opts.player)
    if not character then
        error("spawnEntity needs a player: the build path reads the builder's skills (on a server: a connected player, opts.player)")
    end
    local nSprite = 1
    if opts.face then
        local f = string.upper(tostring(opts.face))
        if not (ENTITY_FACE_INDEX[f] and info:getFace(f)) then
            local have = {}
            for _, name in ipairs(FACES) do
                if info:getFace(name) then have[#have + 1] = name end
            end
            error(string.format("entity '%s' has no face '%s' (faces: %s)", scriptName, tostring(opts.face), table.concat(have, ", ")))
        end
        nSprite = ENTITY_FACE_INDEX[f]
    end
    local recipe = info:getRecipe() and info:getRecipe():getCraftRecipe()
    -- the BuildLogic ISBuildIsoEntity:new makes itself, minus setRecipe(false) when there is no recipe
    local logic = BuildLogic.new(character, nil, nil)
    logic:setContainers(ArrayList.new())
    if recipe then logic:setRecipe(recipe) end
    local build = ISBuildIsoEntity:new(character, info, nSprite, ArrayList.new(), logic)
    build.player = character:getPlayerNum()
    build.haveMaterial = function() return true end
    build:getSprite() -- sets build.north from the rotation, as the server does before create()
    local face = build:getFace()
    if not face then error("entity '" .. scriptName .. "' has no face to build") end
    local tiles = {}
    for zz = 0, face:getzLayers() - 1 do
        for xx = 0, face:getWidth() - 1 do
            for yy = 0, face:getHeight() - 1 do
                local tileInfo = face:getTileInfo(xx, yy, zz)
                if tileInfo and zz == 0 and (tileInfo:getSpriteName() or tileInfo:isBlocking()) then
                    squareOrError(x + xx, y + yy, z)
                end
                if tileInfo and tileInfo:getSpriteName() then tiles[#tiles + 1] = { xx, yy, zz, tileInfo } end
            end
        end
    end
    build:ensureSquaresExist(x, y, z) -- squares of upper layers, as the build cursor makes them
    if not opts.force and not build:isValid(PZLab.square(x, y, z)) then
        error(string.format("vanilla's build check (ISBuildIsoEntity:isValid) refuses '%s' facing %s at %d,%d,%d: "
            .. "something in the way, no floor or a wall between its squares (opts.force skips the check)",
            scriptName, string.upper(face:getFaceName()), x, y, z))
    end
    local openFace = build:getOpenFace(build.north)
    if openFace and (openFace:getWidth() ~= face:getWidth() or openFace:getHeight() ~= face:getHeight()) then openFace = nil end
    local parts, transmit, master = {}, {}, nil
    local function place()
        for _, t in ipairs(tiles) do
            local tileInfo = t[4]
            local sq = PZLab.square(x + t[1], y + t[2], z + t[3])
            local made
            if recipe then
                local before = objectSet(sq)
                local openInfo = openFace and openFace:getTileInfo(t[1], t[2], t[3])
                build:setInfo(sq, build.north, tileInfo:getSpriteName(), openInfo and openInfo:getSpriteName() or false)
                made = newObjects(sq, before)
            else
                made = placeAsMap(sq, tileInfo:getSpriteName())
            end
            for _, obj in ipairs(made) do
                track("object", obj)
                parts[#parts + 1] = obj
                if not recipe then transmit[obj] = true end
                if tileInfo:isMaster() and not master then master = obj end
            end
        end
    end
    -- A light source (script lightRadius, e.g. a lamp on a pillar) comes from the consumed torch,
    -- or under build cheat from the script's debugItem: cheat on for the placement only.
    local cheat = recipe and info:getScript():getLightRadius() > 0 and not character:isBuildCheat()
    if cheat then character:setBuildCheat(true) end
    local ok, err = pcall(place)
    if cheat then character:setBuildCheat(false) end
    if not ok then error(err) end
    if #parts == 0 then error("building '" .. scriptName .. "' made no object") end
    finishObjects(parts, transmit, opts.contents)
    return master or parts[1], parts
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
        if obj ~= f.obj and obj:hasModData() and obj:getModData().pzlabFixture == f.mark then
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
