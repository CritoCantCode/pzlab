-- PZLab: joining a multiplayer server from the main menu, unattended (lab.join / pzlab.py join).
-- Uses the vanilla Direct Connect path: MainScreen:presentServerConnectPopup, the popup's fields,
-- its Connect button (ConnectToServer:connect -> serverConnect). After vanilla's OnConnected the
-- client shows the spawn-region list (only when the server offers several regions), then
-- character creation (profession screen, then appearance screen) when the server has no
-- character for this account, else loads straight in. A tick hook presses each screen's
-- next/play button (ISButton:forceClick, so a disabled button is never pressed) once the screen
-- has been visible for SETTLE_TICKS. Progress and failures are in PZLab.connectState, which the
-- heartbeat (state.json "connect") carries.
--
-- Right after connecting, the client resets its Lua state and loads the server's mods (this file
-- included), before character creation. The request is therefore kept in pzlab/join.txt: a fresh
-- Lua state that finds it while connected (isClient()) and not yet in a game resumes driving the
-- screens. It is emptied once the player is in the world or the join failed.

require "PZLab/PZLab_Core"
require "PZLab/PZLab_Bridge"

local SETTLE_TICKS = 20
local MAX_EVENTS = 24

local function note(text)
    local s = PZLab.connectState
    if not s then return end
    s.events[#s.events + 1] = tostring(getTimestampMs() - s.started) .. "ms " .. text
    if #s.events > MAX_EVENTS then table.remove(s.events, 1) end
    PZLab.log("join: " .. text)
end

local function fail(message)
    local s = PZLab.connectState
    if not s or s.phase == "failed" then return end
    s.phase = "failed"
    s.message = message
    note("failed: " .. tostring(message))
    PZLab.writeFile("join.txt", "")
end

-- join.txt: "key=value" lines (user, ip, port, started).
local function saveRequest(s)
    PZLab.writeFile("join.txt", "user=" .. tostring(s.user) .. "\nip=" .. tostring(s.ip) .. "\nport="
        .. tostring(s.port) .. "\nstarted=" .. tostring(s.started) .. "\n")
end

local function loadRequest()
    local text = PZLab.readFile("join.txt")
    if not text or text == "" then return nil end
    local r = {}
    for key, value in string.gmatch(text, "(%w+)=([^\n]*)") do r[key] = value end
    return r.user and r
end

-- Vanilla connection events (reload-safe: one named handler per event, replaced on reload).
PZLab.joinHandlers = PZLab.joinHandlers or {}
local function on(eventName, fn)
    if PZLab.joinHandlers[eventName] then Events[eventName].Remove(PZLab.joinHandlers[eventName]) end
    PZLab.joinHandlers[eventName] = fn
    Events[eventName].Add(fn)
end

local function onConnected()
    local s = PZLab.connectState
    if s and s.phase == "connecting" then s.phase = "connected" end
    note("OnConnected")
end

-- Vanilla's own handler (registered before this file's) has put the reason on the connect
-- screen; OnConnectFailed often carries no message (e.g. nobody listening at that address).
local function shownReason()
    local screen = ConnectToServer and ConnectToServer.instance
    local label = screen and screen.connectLabel
    return label and label.name or nil
end

local function onConnectFailed(message, detail)
    local text = message or shownReason() or "connection failed"
    fail(tostring(text) .. (detail and detail ~= "" and (" / " .. tostring(detail)) or ""))
end

local function onConnectionStateChanged(state, message)
    note("state " .. tostring(state) .. (message and (" " .. tostring(message)) or ""))
    if state == "Failed" then fail("connection state Failed: " .. tostring(message)) end
end

local function onDisconnect(message)
    local s = PZLab.connectState
    if s and s.phase ~= "loading" and s.phase ~= "ingame" then
        fail("disconnected: " .. tostring(message or shownReason()))
    end
end

on("OnConnected", onConnected)
on("OnConnectFailed", onConnectFailed)
on("OnConnectionStateChanged", onConnectionStateChanged)
on("OnDisconnect", onDisconnect)

-- Starts joining. cfg = { ip, port, user, password, serverPassword }. Call at the main menu.
-- Returns PZLab.connectState: { phase = "connecting" | "connected" | "creating" | "loading" |
-- "failed", message, screen, pressed = { screen names }, events = { "ms text" } }.
function PZLab.joinServer(cfg)
    if getSpecificPlayer(0) then error("already in a game; join from the main menu") end
    if not (MainScreen and MainScreen.instance and ConnectToServer and ConnectToServer.instance) then
        error("main menu not ready")
    end
    -- a failed attempt leaves the connect screen up: leave it as its Back button does
    if ConnectToServer.instance:isVisible() then ConnectToServer.instance:onBackButton() end
    PZLab.connectState = { phase = "connecting", user = cfg.user, ip = cfg.ip, port = tostring(cfg.port),
                           started = getTimestampMs(), events = {}, pressed = {} }
    saveRequest(PZLab.connectState)
    local popup = ServerConnectPopup and ServerConnectPopup.instance
    if popup and popup.connectBtn then
        MainScreen.instance:presentServerConnectPopup()
        popup:setServer(cfg.ip, tostring(cfg.port), cfg.serverPassword or "")
        popup.usernameEntry:setText(cfg.user)
        popup.passwordEntry:setText(cfg.password or "")
        popup.serverPasswordEntry:setText(cfg.serverPassword or "")
        if not popup:checkFields() then error("the connect popup rejected the username") end
        note("connect via ServerConnectPopup " .. cfg.ip .. ":" .. tostring(cfg.port) .. " as " .. cfg.user)
        popup.connectBtn:forceClick()
    else
        MainScreen.instance.bottomPanel:setVisible(false)
        note("connect via ConnectToServer " .. cfg.ip .. ":" .. tostring(cfg.port) .. " as " .. cfg.user)
        ConnectToServer.instance:connect(MainScreen.instance.bottomPanel, "", cfg.user, cfg.password or "",
            cfg.ip, "", tostring(cfg.port), cfg.serverPassword or "", false, true, 1)
    end
    return PZLab.connectState
end

local function visible(el)
    return el ~= nil and el.javaObject ~= nil and el:isVisible()
end

-- The join screen now showing and the button that moves on from it.
local function currentScreen()
    local main = MainScreen and MainScreen.instance
    if MapSpawnSelect and visible(MapSpawnSelect.instance) then
        local m = MapSpawnSelect.instance
        if m.listbox and (m.listbox.selected or 0) < 1 and #m.listbox.items > 0 then m.listbox.selected = 1 end
        return "MapSpawnSelect", m.nextButton
    end
    if main and visible(main.charCreationProfession) then
        return "CharacterCreationProfession", main.charCreationProfession.playButton
    end
    if main and visible(main.charCreationMain) then
        return "CharacterCreationMain", main.charCreationMain.playButton
    end
    if ConnectToServer and visible(ConnectToServer.instance) then
        return "ConnectToServer", nil
    end
    return nil, nil
end

-- A fresh Lua state in the middle of a join (see the top of this file): carry on.
if not PZLab.connectState and isClient() and not getSpecificPlayer(0) then
    local r = loadRequest()
    if r then
        PZLab.connectState = { phase = "connected", resumed = true, user = r.user, ip = r.ip, port = r.port,
                               started = tonumber(r.started) or getTimestampMs(), events = {}, pressed = {} }
        note("resumed after the Lua reload")
    end
end

PZLab.tickHooks.join = function()
    local s = PZLab.connectState
    if not s or s.phase == "failed" or s.phase == "ingame" then return end
    if getSpecificPlayer(0) then
        s.phase = "ingame"
        note("in game")
        PZLab.writeFile("join.txt", "")
        return
    end
    if s.phase == "loading" then return end
    local screen, button = currentScreen()
    if screen ~= s.screen then
        s.screen, s.screenTicks = screen, 0
        if screen then note("screen " .. screen) end
    end
    if not screen or not button then return end
    s.screenTicks = s.screenTicks + 1
    if s.screenTicks ~= SETTLE_TICKS then return end
    if not (button.enable and button:isVisible()) then
        note(screen .. ": next button disabled (" .. tostring(button.tooltip) .. ")")
        return
    end
    s.phase = screen == "CharacterCreationMain" and "loading" or "creating"
    s.pressed[#s.pressed + 1] = screen
    note("press " .. screen .. " next")
    button:forceClick()
end
