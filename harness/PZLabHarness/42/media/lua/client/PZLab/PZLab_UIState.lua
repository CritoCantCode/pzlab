-- PZLab UI state and recovery: what would intercept input right now (the in-game pause menu,
-- context menus, modal dialogs, a focused text entry, a drag, a paused clock) and vanilla-path
-- ways back to a playable state. Tests call PZLab.uiState() to notice a bad state and
-- PZLab.ensurePlayable() to start from a known-good one. Modal dialogs are only reported, never
-- answered: accepting or declining one is a decision for the test (or its person) to make.

require "PZLab/PZLab_Core"
require "PZLab/PZLab_Bridge"
require "PZLab/PZLab_UI"

local MAX_TEXT = 300

local function className(obj)
    local ok, name = pcall(getClassSimpleName, obj)
    return ok and tostring(name) or "?"
end

local function tableOf(j)
    local ok, t = pcall(function() return j:getTable() end)
    if ok then return t end
    return nil
end

local function truncate(s)
    if type(s) ~= "string" then return nil end
    if #s > MAX_TEXT then return string.sub(s, 1, MAX_TEXT) .. "..." end
    return s
end

-- An ISUIElement-derived table (what vanilla and most mods build). B42 PZAPI "atom" UI tables
-- hold their Java object in javaObj and resolve fields through metatable code, so they are
-- described by Java class instead of being read like ISUIElement tables.
local function isLuaElement(t)
    return type(t) == "table" and rawget(t, "javaObject") ~= nil
end

-- Java object behind a UI table (ISUIElement or atom), or the value itself.
local function javaOfAny(el)
    if type(el) == "table" then return rawget(el, "javaObject") or rawget(el, "javaObj") end
    return el
end

-- Short description of a top-level element (Lua table and/or Java element).
local function describe(t, j)
    local lua = isLuaElement(t)
    j = j or javaOfAny(t)
    local d = { type = lua and t.Type or (j and className(j)) or "?", handle = PZLab.handle(t or j) }
    if lua and type(t.title) == "string" then d.title = t.title end
    if j then
        -- getUIName exists on both implementations of UIElementInterface (UIElement, AtomUI)
        local name = tostring(j:getUIName() or "")
        if name ~= "" and name ~= d.type then d.name = name end
    end
    return d
end

---------------------------------------------------------------------------------------------
-- Pause menu (MainScreen.instance with inGame = true, toggled by vanilla's ToggleEscapeMenu)
---------------------------------------------------------------------------------------------

-- True while the in-game Escape menu is up.
function PZLab.isPauseMenuOpen()
    local ms = MainScreen and MainScreen.instance
    return ms ~= nil and ms.inGame == true and ms:getIsVisible() == true
end

local function escapeMenuKey()
    local key = getCore():getKey(KeybindId.MAIN_MENU)
    if key == nil or key == 0 then key = Keyboard.KEY_ESCAPE end
    return key
end

-- Leaves the in-game pause menu the way its "Resume" item does: that item's own click handler
-- (MainScreen.onMenuItemMouseDownMainMenu, internal "RETURN"), which calls vanilla
-- ToggleEscapeMenu: it restores the UI the menu hid (ISUIHandler.setVisibleAllUI) and sets game
-- speed 1. Falls back to ToggleEscapeMenu itself when the item's handler returned early.
-- Returns (closed, problem) without raising: closed is true when it left the menu.
local function leavePauseMenu()
    if not PZLab.isPauseMenuOpen() then return false end
    local ms = MainScreen.instance
    if MainOptions and MainOptions.instance and MainOptions.instance.modal then
        return false, "the options screen's confirm-changes dialog is open; vanilla won't leave the menu until it is answered"
    end
    local item = ms.returnOption
    if item and type(item.onMouseDown) == "function" then
        item:onMouseDown(0, 0)
    end
    -- ToggleEscapeMenu cancels a build/placement cursor (getCell():getDrag) instead of
    -- toggling when one is set, so a second call may be needed.
    for _ = 1, 2 do
        if not PZLab.isPauseMenuOpen() then break end
        ToggleEscapeMenu(escapeMenuKey())
    end
    if PZLab.isPauseMenuOpen() then return false, "the pause menu is still open" end
    return true
end

-- Returns true when it closed the menu, false when the menu wasn't open; raises if it stays open.
function PZLab.closePauseMenu()
    local closed, problem = leavePauseMenu()
    if problem then error("closePauseMenu: " .. problem) end
    return closed
end

---------------------------------------------------------------------------------------------
-- Game speed
---------------------------------------------------------------------------------------------

-- GameTime multipliers vanilla pairs with each speed (SpeedControlsHandler / SpeedControls
-- buttons): setGameSpeed(n) alone resets the multiplier to 1.
local SPEED_MULTIPLIER = { 1, 5, 20, 40 }

-- Sets game speed like vanilla's speed keys: 0 pause, 1 normal, 2/3/4 fast forward (x5, x20,
-- x40 time multiplier). Returns getGameSpeed().
function PZLab.setSpeed(speed)
    speed = math.floor(tonumber(speed) or 1)
    if speed < 0 or speed > 4 then error("setSpeed: speed must be 0..4, got " .. tostring(speed)) end
    setGameSpeed(speed)
    if speed > 0 then getGameTime():setMultiplier(SPEED_MULTIPLIER[speed]) end
    return getGameSpeed()
end

---------------------------------------------------------------------------------------------
-- Context menus (ISContextMenu: each player's menu from getPlayerContextMenu, its submenus,
-- and any menu a mod creates; every one is a top-level UI element)
---------------------------------------------------------------------------------------------

-- Visible, non-empty context menus, back to front. (Vanilla parks an empty menu off-screen.)
local function visibleContextMenus()
    local out = {}
    local list = UIManager.getUI()
    for i = 0, list:size() - 1 do
        local j = list:get(i)
        if j:isVisible() then
            local t = tableOf(j)
            if isLuaElement(t) and PZLab.isA(t, "ISContextMenu") and not t:isEmpty() then
                out[#out + 1] = t
            end
        end
    end
    return out
end

-- Closes every visible context menu with vanilla's own calls: each player's menu with
-- ISContextMenu:closeAll() (what choosing an option does: the menu, its submenus and their
-- tooltips), then any other visible ISContextMenu with hideAndChildren(). No key events.
-- Returns how many visible menus (submenus included) were closed.
-- While the pause menu is up, menus it hid are invisible here and come back when it closes:
-- close the pause menu first (ensurePlayable does).
function PZLab.closeContextMenus()
    local before = #visibleContextMenus()
    if before == 0 then return 0 end
    for p = 0, 3 do
        local ok, menu = pcall(getPlayerContextMenu, p)
        if ok and menu then
            local shown = menu:isVisible()
            for _, sub in ipairs(menu.instanceMap or {}) do shown = shown or sub:isVisible() end
            if shown then menu:closeAll() end
        end
    end
    for _, t in ipairs(visibleContextMenus()) do
        t:hideAndChildren()
    end
    return before - #visibleContextMenus()
end

-- Visible context menus with their options, back to front (front-most last):
-- { handle, player, sub (a submenu), rect, options = { { index, name, disabled, subMenu,
--   point = {x, y} (centre of the row: hover/click here; nil when scrolled out of view) } } }
-- Positions use the menu's final place (vanilla may slide a new menu in over ~100 ms).
function PZLab.contextMenus()
    local out = {}
    for _, t in ipairs(visibleContextMenus()) do
        local j = t.javaObject
        local x, y = t.slideGoalX or j:getX(), t.slideGoalY or j:getY()
        local w = j:getWidth()
        local dy = (t:getScrollHeight() > t:getScrollAreaHeight()) and t.scrollIndicatorHgt or 0
        local areaTop = y + t.padTopBottom + dy
        local areaBottom = areaTop + t:getScrollAreaHeight()
        local options = {}
        for i, opt in ipairs(t.options) do
            local top = areaTop + (opt.id - 1) * t.itemHgt + j:getYScroll()
            local o = {
                index = i, name = opt.name,
                disabled = (opt.notAvailable or opt.isDisabled) and true or false,
                subMenu = opt.subOption ~= nil,
            }
            if top >= areaTop and top + t.itemHgt <= areaBottom then
                o.point = { x = math.floor(x + math.min(w / 2, 60)), y = math.floor(top + t.itemHgt / 2) }
            end
            options[#options + 1] = o
        end
        out[#out + 1] = {
            handle = PZLab.handle(t), player = t.player, sub = t.parent ~= nil,
            rect = { x = x, y = y, w = w, h = j:getHeight() }, options = options,
        }
    end
    return out
end

---------------------------------------------------------------------------------------------
-- Modal dialogs
---------------------------------------------------------------------------------------------

-- Lua UI Types reported as modal dialogs (derived classes included). Any Type whose name
-- contains "Modal" or "Dialog" counts too, which covers most mods' own dialogs; add other
-- Types here (PZLab.modalTypes[#PZLab.modalTypes + 1] = "MyPrompt").
PZLab.modalTypes = PZLab.modalTypes or {
    "ISModalDialog", "ISModalRichText", "ISTextBox", "ISRichTextBox", "ISCollapsableModalRichText",
    "ISTextBoxMap", "ISSetKeybindDialog", "ISDuplicateKeybindDialog", "ISSleepDialog",
    "ISAlarmClockDialog", "ISBombTimerDialog", "ISModalEditRole",
}

local function isModal(t)
    local typeName = t.Type
    if type(typeName) == "string" and (string.find(typeName, "Modal", 1, true) or string.find(typeName, "Dialog", 1, true)) then
        return true
    end
    for _, name in ipairs(PZLab.modalTypes) do
        if PZLab.isA(t, name) then return true end
    end
    return false
end

local function buttonsOf(t)
    local out = {}
    for _, b in ipairs(PZLab.findUI("ISButton", { root = t })) do
        if type(b) == "table" and b.javaObject then
            local r = PZLab.visibleRect(b)
            out[#out + 1] = {
                title = type(b.title) == "string" and b.title or nil,
                internal = type(b.internal) == "string" and b.internal or nil,
                enabled = b.enable ~= false,
                point = { x = math.floor(r.x + r.w / 2), y = math.floor(r.y + r.h / 2) },
            }
        end
    end
    return out
end

-- Visible modal dialogs, front-most first: { type, title, name, text (truncated), handle,
-- buttons = { { title, internal, enabled, point = {x, y} } } }. A Java-side ModalDialog
-- (UIManager.getModal()) is reported with type "ModalDialog".
function PZLab.modals()
    local out = {}
    local list = UIManager.getUI()
    for i = list:size() - 1, 0, -1 do
        local j = list:get(i)
        if j:isVisible() then
            local t = tableOf(j)
            if isLuaElement(t) and isModal(t) then
                local d = describe(t, j)
                d.text = truncate(t.text)
                d.buttons = buttonsOf(t)
                out[#out + 1] = d
            end
        end
    end
    local ok, modal = pcall(function() return UIManager.getModal() end)
    if ok and modal and modal:isVisible() then
        local d = describe(nil, modal)
        d.type = "ModalDialog"
        out[#out + 1] = d
    end
    return out
end

---------------------------------------------------------------------------------------------
-- Mouse and keyboard targets
---------------------------------------------------------------------------------------------

-- The front-most visible top-level element that would take a mouse click at (x, y) (default:
-- the mouse) instead of the world, following UIManager.updateMouseButtons / isOverElement: an
-- element capturing the mouse, or one whose rectangle (clipped to maxDrawHeight, how vanilla
-- collapses windows) holds the point and that consumes mouse events. Returns
-- { type, title, name, handle, capture } or false.
function PZLab.uiAt(x, y)
    x, y = x or getMouseX(), y or getMouseY()
    local list = UIManager.getUI()
    for i = list:size() - 1, 0, -1 do
        local j = list:get(i)
        if j:isVisible() then
            local capture = j:isCapture() == true
            local over = capture
            if not over then
                local ex, ey, w, h = j:getX(), j:getY(), j:getWidth(), j:getHeight()
                local maxH = j:getMaxDrawHeight()
                if maxH and maxH ~= -1 then h = math.min(h, maxH) end
                over = x >= ex and y >= ey and x < ex + w and y < ey + h
            end
            if over then
                -- isConsumeMouseEvents exists on UIElement only (not on AtomUI elements)
                if capture or not instanceof(j, "UIElement") or j:isConsumeMouseEvents() then
                    local d = describe(tableOf(j), j)
                    d.capture = capture
                    return d
                end
            end
        end
    end
    return false
end

-- What would take a click at (x, y) instead of element el (table, Java element or handle):
-- false when the front-most element there (uiAt) is el's own top-level window; otherwise
-- uiAt's description of the element in front, or { type = "world" } when no UI takes the
-- click there.
function PZLab.coveredBy(el, x, y)
    local at = PZLab.uiAt(x, y)
    if not at then return { type = "world" } end
    if at.handle == PZLab.handle(PZLab.uiRoot(PZLab.uiElement(el))) then return false end
    return at
end

-- Class checks first: in -debug with "Break On Error" on, even an error caught by pcall halts
-- all Lua in the debugger, so probes here must never raise.
local function isTextEntry(j)
    return j ~= nil and (instanceof(j, "UITextBox2") or instanceof(j, "AtomUITextEntry"))
end

local function isTyping(el)
    local j = javaOfAny(el)
    return isTextEntry(j) and j:isDoingTextEntry() == true
end

-- The focused text entry that is taking keystrokes, or false: { type, handle, text,
-- root = { type, title, handle } (its window) }. Java tracks it in the static
-- Core.currentTextEntryBox, which Lua can't read (exposed static fields are a snapshot), so
-- this asks getCore():isDoingTextEntry() and then finds the element in the UI tree.
function PZLab.textFocus()
    if not getCore():isDoingTextEntry() then return false end
    local box = PZLab.findUI(isTyping, { visibleOnly = false, limit = 1 })[1]
    if not box then
        -- e.g. a dialog destroyed (removed from the UI) while its entry had focus: vanilla
        -- doesn't unfocus it, so it keeps swallowing keys until a left click or unfocus.
        return { type = "unknown", detached = true }
    end
    local t = type(box) == "table" and box or nil
    local j = javaOfAny(box)
    local d = describe(t, j)
    d.title = nil -- an ISTextEntryBox's title is its initial text
    d.text = truncate(tostring(j:getText()))
    local root = j
    while root:getParent() do root = root:getParent() end
    if root ~= j then d.root = describe(tableOf(root), root) end
    return d
end

-- What a press of Escape would do right now, following GameKeyboard.update:
--   "textEntry"            a focused text entry takes the key
--   "consumed:<Type>"      a visible top-level element that wants key events consumes it
--                          (Lua isKeyConsumed; many windows close themselves on Escape)
--   "cancelPlacement"      vanilla ToggleEscapeMenu drops the build/placement cursor
--   "openPauseMenu" / "closePauseMenu"   vanilla ToggleEscapeMenu
--   "menu"                 at the main menu (MainScreen handles it), "none" otherwise
function PZLab.escapeAction()
    if getCore():isDoingTextEntry() then return "textEntry" end
    local list = UIManager.getUI()
    for i = list:size() - 1, 0, -1 do
        local j = list:get(i)
        local okWant, want = pcall(function() return j:isVisible() and j:isWantKeyEvents() end)
        if okWant and want then
            local t = tableOf(j)
            if t and type(t.isKeyConsumed) == "function" then
                local ok, consumed = pcall(t.isKeyConsumed, t, Keyboard.KEY_ESCAPE)
                if ok and consumed then return "consumed:" .. tostring(t.Type) end
            end
        end
    end
    local ms = MainScreen and MainScreen.instance
    if not (ms and ms.inGame) then return ms and "menu" or "none" end
    if getCell() and getCell():getDrag(0) then return "cancelPlacement" end
    if MainOptions and MainOptions.instance and MainOptions.instance.modal then return "none" end
    return ms:getIsVisible() and "closePauseMenu" or "openPauseMenu"
end

---------------------------------------------------------------------------------------------
-- The whole picture
---------------------------------------------------------------------------------------------

-- Snapshot of everything that decides where input goes:
-- { ingame, mainMenu, pauseMenu, contextMenus (count, submenus included), modals (list, see
--   PZLab.modals), speed (0 paused .. 4), multiplier (GameTime), paused (isGamePaused),
--   mouse = {x, y}, mouseOverUI (PZLab.uiAt() at the mouse: false or {type, ...}),
--   focus (PZLab.textFocus(): false or {type, ...}), drag (false | "items" | "placement"),
--   escape (PZLab.escapeAction()), problems = { ... }, playable = (#problems == 0) }
-- problems lists what would make world input misbehave: "notInGame", "pauseMenu", "paused",
-- "contextMenus", "modal:<Type>", "textEntry", "drag:items", "drag:placement".
function PZLab.uiState()
    local player = getSpecificPlayer(0)
    local ms = MainScreen and MainScreen.instance
    local s = {
        ingame = player ~= nil,
        mainMenu = ms ~= nil and ms.inGame ~= true and ms:getIsVisible() == true,
        pauseMenu = PZLab.isPauseMenuOpen(),
        contextMenus = #visibleContextMenus(),
        modals = PZLab.modals(),
        speed = getGameSpeed(),
        paused = isGamePaused() == true,
        mouse = { x = getMouseX(), y = getMouseY() },
        mouseOverUI = PZLab.uiAt(),
        focus = PZLab.textFocus(),
        drag = false,
        escape = PZLab.escapeAction(),
    }
    local okMul, mul = pcall(function() return getGameTime():getTrueMultiplier() end)
    if okMul then s.multiplier = mul end
    if ISMouseDrag and ISMouseDrag.dragging ~= nil then
        s.drag = "items"
    elseif player and getCell() and getCell():getDrag(0) then
        s.drag = "placement"
    end

    local problems = {}
    if not s.ingame then problems[#problems + 1] = "notInGame" end
    if s.pauseMenu then problems[#problems + 1] = "pauseMenu" end
    if s.ingame and s.paused then problems[#problems + 1] = "paused" end
    if s.contextMenus > 0 then problems[#problems + 1] = "contextMenus" end
    for _, m in ipairs(s.modals) do problems[#problems + 1] = "modal:" .. tostring(m.type) end
    if s.focus then problems[#problems + 1] = "textEntry" end
    if s.drag then problems[#problems + 1] = "drag:" .. s.drag end
    s.problems = problems
    s.playable = #problems == 0
    return s
end

-- Gets back to a playable state the way a person would: leaves the pause menu (its Resume
-- path), closes context menus, takes keyboard focus off a text entry (Core.
-- UnfocusActiveTextEntryBox, what any left click does; the text and its dialog stay), sets game
-- speed (opts.speed, default 1; vanilla's mapping, see setSpeed). Never answers modal dialogs
-- or cancels drags; those stay in after.problems. Returns { before = uiState, after = uiState,
-- closed = { pauseMenu, contextMenus, textEntry }, errors = { ... } (only when a step failed) }.
function PZLab.ensurePlayable(opts)
    opts = opts or {}
    local before = PZLab.uiState()
    local errors = {}
    local closedMenu, problem = leavePauseMenu()
    if problem then errors[#errors + 1] = "pause menu: " .. problem end
    local closed = { pauseMenu = closedMenu, contextMenus = PZLab.closeContextMenus(), textEntry = false }
    if getCore():isDoingTextEntry() then
        Core.UnfocusActiveTextEntryBox()
        closed.textEntry = true
    end
    if getSpecificPlayer(0) then PZLab.setSpeed(opts.speed or 1) end
    local result = { before = before, after = PZLab.uiState(), closed = closed }
    if #errors > 0 then result.errors = errors end
    return result
end
