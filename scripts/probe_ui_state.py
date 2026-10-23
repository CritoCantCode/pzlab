"""Smoke test for UI state and recovery: uiState, contextMenus, closeContextMenus,
closePauseMenu, ensurePlayable, setSpeed, mouseOverUI, pickBlockedBy and text focus.

Opens a world context menu on a floor tile next to the player, closes it the vanilla way, sends
Escape with nothing open (that opens the pause menu and pauses the game), recovers with
ensure_playable and proves world input works again with another right-click. Then checks
mouseOverUI and pickBlockedBy over an inventory window, over the world and over a click-through
panel, and text focus with a throwaway ISTextBox this script creates and destroys itself.

Run: python pzlab.py run scripts/probe_ui_state.py
Keep the real mouse cursor off the game window (see AGENT.md traps)."""

import time

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", name, detail))


def menus_open():
    return lab.ui_state()["contextMenus"]


def right_click_floor():
    """Right-clicks the centre of the first neighbouring floor tile the engine picks."""
    for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1), (1, 1)):
        p = lab.lua("""local sq = PZLab.player():getSquare()
            return PZLab.toScreen(sq:getX() + 0.5 + %d, sq:getY() + 0.5 + %d, sq:getZ())""" % (dx, dy))
        lab.input.move(p["x"], p["y"])
        time.sleep(0.15)
        if lab.lua("return UIManager.getLastPicked() ~= nil"):
            lab.input.click(p["x"], p["y"], "right")
            time.sleep(0.4)
            return p
    return None


start = lab.ensure_playable()
check("baseline playable", start["after"]["playable"], start["after"]["problems"])

# Context menu: open, inspect, hover an option, close the vanilla way.
point = right_click_floor()
state = lab.ui_state()
check("right-click opens a world context menu", point is not None and state["contextMenus"] > 0, point)
check("problems list it", "contextMenus" in state["problems"], state["problems"])
menus = lab.context_menus()
opt = next((o for m in menus for o in m["options"] if o.get("point")), None)
if opt:
    lab.input.move(opt["point"]["x"], opt["point"]["y"])
    time.sleep(0.25)
    hovered = lab.lua("return PZLab.uiElement(%d).mouseOver" % menus[0]["handle"])
    check("option point hovers that option", hovered == opt["index"], (opt["name"], hovered))
closed = lab.close_context_menus()
check("closeContextMenus closes them", closed > 0 and menus_open() == 0, closed)

# Escape with nothing open opens the pause menu and pauses the game.
check("escape prediction", lab.ui_state()["escape"] == "openPauseMenu")
lab.input.tap("esc")
time.sleep(0.5)
state = lab.ui_state()
check("esc opened the pause menu", state["pauseMenu"] and state["speed"] == 0 and state["paused"],
      {k: state[k] for k in ("pauseMenu", "speed", "paused", "escape")})
shot = lab.screenshot(check=True)
check("screenshot(check=True) reports it", "pauseMenu" in shot["problems"], shot)
right_click_floor()
check("world input is blocked by the pause menu", menus_open() == 0)

fixed = lab.ensure_playable()
after = fixed["after"]
check("ensure_playable leaves the pause menu", fixed["closed"]["pauseMenu"] and not after["pauseMenu"]
      and after["speed"] == 1 and not after["paused"] and after["playable"], fixed["closed"])
right_click_floor()
check("world input works again", menus_open() > 0)

# Escape does not close a context menu: the pause menu hides it, Resume brings it back.
lab.input.tap("esc")
time.sleep(0.5)
state = lab.ui_state()
check("esc with a menu open opens the pause menu (menu hidden)", state["pauseMenu"] and state["contextMenus"] == 0)
check("close_pause_menu", lab.close_pause_menu() is True)
check("...and the hidden context menu is back", menus_open() > 0)
fixed = lab.ensure_playable()
check("ensure_playable closes it", fixed["closed"]["contextMenus"] > 0 and fixed["after"]["playable"], fixed["closed"])

# Speeds.
fast = lab.ensure_playable(speed=2)["after"]
check("speed 2 = fast forward x5", fast["speed"] == 2 and fast["multiplier"] == 5, (fast["speed"], fast["multiplier"]))
normal = lab.ensure_playable()["after"]
check("back to speed 1", normal["speed"] == 1 and normal["multiplier"] == 1)

# mouseOverUI over an inventory window and over the world.
inv, _ = lab.lua("return PZLab.openInventoryWindows()")
time.sleep(0.3)
drop = next(p for p in lab.inventory_panes() if p["handle"] == inv)["drop"]
lab.input.move(drop["x"], drop["y"])
time.sleep(0.2)
state = lab.ui_state()
over, blocked = state["mouseOverUI"], state["pickBlockedBy"]
check("mouseOverUI over the inventory window", over and over["type"] == "ISInventoryPage", over)
check("pickBlockedBy over the inventory window (not click-through)",
      blocked and blocked["type"] == "ISInventoryPage" and blocked["clickThrough"] is False, blocked)
p = lab.lua("return PZLab.toScreen(PZLab.player():getX(), PZLab.player():getY() + 1, PZLab.player():getZ())")
lab.input.move(p["x"], p["y"])
time.sleep(0.2)
state = lab.ui_state()
check("mouseOverUI false over the world", state["mouseOverUI"] is False, state["mouseOverUI"])
check("pickBlockedBy false over the world", state["pickBlockedBy"] is False, state["pickBlockedBy"])
# A click-through panel (setWantMouseEvents(false)) under the mouse: clicks reach the world, but
# the engine picks no world object there.
lab.lua("""local panel = ISPanel:new(%d, %d, 40, 40) panel:initialise() panel:setWantMouseEvents(false)
    panel:addToUIManager() PZLab.probePanel = panel return true""" % (p["x"] - 20, p["y"] - 20))
time.sleep(0.2)
state = lab.ui_state()
picked = lab.lua("return UIManager.getLastPicked() ~= nil")
lab.lua("PZLab.probePanel:removeFromUIManager() PZLab.probePanel = nil return true")
check("click-through panel: mouseOverUI false, pickBlockedBy names it, no world pick",
      state["mouseOverUI"] is False and (state["pickBlockedBy"] or {}).get("type") == "ISPanel"
      and state["pickBlockedBy"]["clickThrough"] is True and picked is False,
      (state["mouseOverUI"], state["pickBlockedBy"], picked))

# Text focus and modal dialogs (a dialog this script owns).
lab.lua("""local box = ISTextBox:new(400, 250, 320, 160, "pzlab probe", "text", nil, nil, 0)
    box:initialise() box:addToUIManager() box.entry:focus() PZLab.probeBox = box return true""")
state = lab.ui_state()
check("focus reports the text entry", state["focus"] and state["focus"]["type"] == "ISTextEntryBox", state["focus"])
check("escape goes to the text entry", state["escape"] == "textEntry")
modal = next((m for m in state["modals"] if m["type"] == "ISTextBox"), None)
check("modal reported with its buttons", modal and [b["internal"] for b in modal["buttons"]] == ["OK", "CANCEL"], modal)
fixed = lab.ensure_playable()
check("ensure_playable unfocuses but leaves the dialog", fixed["closed"]["textEntry"] and not fixed["after"]["focus"]
      and "modal:ISTextBox" in fixed["after"]["problems"], fixed["after"]["problems"])
try:
    lab.require_playable()
    check("require_playable raises on a modal", False)
except Exception as e:
    check("require_playable raises on a modal", type(e).__name__ == "NotPlayable", e)
lab.require_playable(allow=("modal",))
lab.lua("PZLab.probeBox:destroy() PZLab.probeBox = nil return true")
check("clean at the end", lab.ui_state()["playable"])

print("%d/%d passed" % (sum(results), len(results)))
