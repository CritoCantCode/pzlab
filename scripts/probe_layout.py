"""Smoke test for UI layout snapshots and the vanilla inventory panes.

Snapshots the layout, then inside lab.ui_layout(): shows, pins, expands, moves and resizes the
vanilla inventory and loot windows (lab.show_inventory_panes), opens a throwaway window, and
finally removes both vanilla windows from the UI (PZLab.closeUI). On leaving the block the
layout is restored; every top-level element must match its snapshot again (position, size,
visible, pinned, collapsed), and the vanilla windows must be back in the UI. Then
restore_layout(hide_added=True) hides a window opened after a snapshot.

Run: python pzlab.py run scripts/probe_layout.py
Keep the real mouse cursor off the game window (an unpinned window expands under the mouse)."""

import time

results = []
FIELDS = ("x", "y", "w", "h", "visible", "pinned", "collapsed", "maxDrawHeight")


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s  %s" % ("PASS" if ok else "FAIL", name, detail))


def by_handle(snapshot):
    return {w["handle"]: w for w in snapshot["windows"]}


def differences(before, after):
    out = []
    now = by_handle(after)
    for h, w in by_handle(before).items():
        cur = now.get(h)
        if cur is None:
            out.append((w["type"], "not in the UI"))
            continue
        for f in FIELDS:
            a, b = w.get(f), cur.get(f)
            if isinstance(a, float) or isinstance(b, float):
                same = a is not None and b is not None and abs(a - b) < 0.5
            else:
                same = a == b
            if not same:
                out.append((w["type"], f, a, b))
    return out


lab.ensure_playable()
lab.input.move(5, 5)
time.sleep(0.3)
before = lab.save_layout()
pages = [w for w in before["windows"] if w["type"] == "ISInventoryPage"]
print("vanilla windows before:", [(w.get("name"), w.get("pinned"), w.get("collapsed"), w["x"], w["y"], w["w"], w["h"])
                                  for w in pages])

with lab.ui_layout() as snap:
    panes = lab.show_inventory_panes(inventory={"x": 60, "y": 80, "w": 420, "h": 380},
                                     loot={"x": 700, "y": 80, "w": 420, "h": 380})
    inv, loot = panes["inventory"], panes["loot"]
    check("panes shown and pinned", inv["info"]["shown"] and loot["info"]["shown"] and lab.lua(
        "return PZLab.get(%d).pin and PZLab.get(%d).pin and not PZLab.get(%d).isCollapsed"
        % (inv["window"], loot["window"], loot["window"])), (inv["rect"], loot["rect"]))
    check("panes placed", inv["rect"] == {"x": 60, "y": 80, "w": 420, "h": 380}, inv["rect"])
    time.sleep(1.5)  # long enough for an unpinned window to collapse again
    check("pinned panes stay expanded with the mouse away",
          lab.lua("return PZLab.paneInfo(%d).shown" % inv["pane"]) is True)
    lab.lua("""local w = ISCollapsableWindow:new(300, 300, 220, 140) w:initialise() w:addToUIManager()
               w.title = 'pzlab probe window' PZLab.probeWindow = w return true""")
    check("closeUI removes the vanilla windows", lab.lua('return PZLab.closeUI("ISInventoryPage")') == 2)

time.sleep(0.3)
after = lab.save_layout()
diff = differences(before, after)
check("layout restored: every element as in the snapshot", not diff, diff)
check("vanilla windows back in the UI", lab.lua(
    "return UIManager.getUI():contains(getPlayerInventory(0).javaObject) and "
    "UIManager.getUI():contains(getPlayerLoot(0).javaObject)"))
check("probe window reported as added, still shown",
      lab.lua("return PZLab.probeWindow:isVisible()") is True)

# hide_added: a window opened after a snapshot is hidden on restore.
snap2 = lab.save_layout()
lab.lua("PZLab.probeWindow2 = ISCollapsableWindow:new(320, 320, 200, 120) PZLab.probeWindow2:initialise() "
        "PZLab.probeWindow2:addToUIManager() return true")
result = lab.restore_layout(snap2, hide_added=True)
check("hide_added hides windows opened since", any(a["type"] == "ISCollapsableWindow" for a in result["added"])
      and lab.lua("return PZLab.probeWindow2:isVisible()") is False, result["added"])

lab.lua("""for _, k in ipairs({ 'probeWindow', 'probeWindow2' }) do
    local w = PZLab[k] if w then w:setVisible(false) w:removeFromUIManager() PZLab[k] = nil end
end return true""")
print("%d/%d passed" % (sum(results), len(results)))
