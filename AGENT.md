# pzlab — agent reference

Drives a real Project Zomboid (Build 42) instance for automated testing of any mod: launch into a
world unattended, run Lua inside the live game, inject mouse/keyboard, take screenshots, read
logs. Single player, or multiplayer: a local dedicated server (the lab server) with one or more
lab clients joined to it, Lua on both sides (see Multiplayer). Built for Claude; terse by design.
Self-contained: everything is in this repository, nothing names a machine, user or mod; the
folder can be copied anywhere.

## Steer the mod toward performance and safety

Whoever you test for is usually building a mod, and players run it alongside many others. Don't
stop at "the tests pass": when you design, review or test, suggest the faster and safer direction,
and say so when a requested approach risks the game's frame rate, stability, saves or other mods.
Explain the risk, offer the alternative, and let the person decide. Back claims with lab
measurements, not intuition; the traps cited below are in "Engine facts and traps".

Safety: a bug in the mod must not crash the game, break vanilla or lose items.
- Guard every entry point (event handlers, wrapped vanilla functions, UI methods, callbacks handed
  to vanilla) with `pcall`, and switch the mod off after repeated errors (a circuit breaker).
  Caught errors are still logged (~100 lines each) and counted, so one error per frame floods
  the log and stalls the game even when caught.
- Wrap vanilla functions (call the original, add before/after behaviour); don't replace them or
  override vanilla files. Other mods and game updates keep working. Wrapping misses callbacks
  vanilla registered with the original function (trap).
- Change game state only through vanilla's own paths: timed actions for items (they are what the
  server validates in multiplayer). Never create, delete or move items directly from UI code.
- Re-validate any held world object (object, square, corpse, vehicle) before using it. Java
  exceptions inside game calls don't raise in Lua: the call returns nil (trap), so check arguments
  and nil results.
- Give everything the mod creates an owner and a teardown: UI elements, highlights (persistent
  ones stay on the object until cleared), held objects, event handlers. Tear down on death,
  character rebuild, exit and hot reload.
- Hot reload reroutes event callbacks by prototype name: register one dispatcher per event,
  outside reloads (trap). No coroutines (they crash Kahlua). Keep Lua source ASCII.
- Multiplayer: the server may not see what the client sees. Check where items really ended up
  (`lab.server.lua`, `PZLab.locate`), not just the client's view.

Performance: Lua runs on the game's frame.
- Per-frame paths (`OnTick`, `OnRenderTick`, `OnPreUIDraw`, UI `update`/`prerender`/`render`) must
  not allocate: reuse tables, cache strings and textures (garbage collection pauses the game).
  Scale the work with what is on screen and skip what isn't.
- Reuse what the game already computes (vanilla's container scans, the engine's picked object)
  instead of scanning squares yourself. Run expensive work on change events or throttled, never
  every frame.
- Don't add a visible full-screen element: any visible top-level UI under the cursor, click-through
  included, stops the engine's world picking (trap).
- Measure: `getTimestampMs()` ticks in 1 ms steps (trap), so time many frames or iterations and
  report µs per frame. Compare with the feature off.

Make these permanent checks in the mod's test suite, not one-off looks:
- After each step, nothing unexpected was logged: `off = lab.log.size()` … `lab.log.exceptions(since=off)`.
- Leaks: `UIManager.getUI():size()` and the mod's own counts return to baseline after a few
  hundred open/close cycles (one per frame), a walk past many objects, a death and a hot reload
  (`pzlab.py reload <file>`; handlers not doubled).
- Failure: force an error in the mod's per-frame path. The mod should switch itself off cleanly
  and vanilla keep working.
- Cost per frame with the feature in use, against a budget (e.g. under 0.5 ms).
- Multiplayer when the mod touches items or world state: `start --join`, checked on both sides.

## Requirements

Windows 10/11, Python 3.8+ (stdlib only; Pillow optional, used for speed), Project Zomboid B42
installed (Steam libraries auto-detected; otherwise `init --game-dir`). The game's bundled Java
runs everything, including the Lua checker — no JDK needed.

## New machine

1. `python pzlab.py doctor` — shows detected game dir, launch command, ToS status.
2. `python pzlab.py init [--game-dir DIR]` — writes `lab.local.json` (gitignored): game dir,
   plus `termsOfServiceVersion`/`language` copied from that person's own `~/Zomboid/options.ini`
   if present. **The lab never accepts the Terms of Service itself**: if nothing was imported,
   a person runs `start --menu` once and clicks ACCEPT in the game window; the lab profile keeps it.
   A stalled start says so explicitly and attaches a window capture.
3. `python pzlab.py start --mod-dir <path to mod or Workshop item>`.

## Run

All commands: `python pzlab.py <cmd>`. Output is JSON.

| Command | Does |
|---|---|
| `init [--game-dir D] [--no-import]`, `doctor` | machine setup / environment report |
| `check PATH...` | Lua syntax check with the game's own Kahlua compiler; no game launch |
| `start [--mod-dir PATH]... [--mod NAME]... [--at X Y Z \| --at LOCATION] [--menu \| --join [--user U --password P]]` | setup + launch + autostart world + dismiss "Click to Start"; ~40 s cold. `--mod-dir` links any mod folder or Workshop item (`Contents/mods/<one mod>`); `--mod` names one from config. `--at` takes a tile or a catalogue name (see Test locations). `--menu` stops at main menu. `--join`: multiplayer instead: starts the lab server if it isn't running (same mods, `--at` = spawn point), launches to the menu and joins it (`join`); ~95 s cold (server ~45 s, join ~35-50 s) |
| `world [--at X Y Z \| --at LOCATION]` | from main menu of a running game, launch a fresh world (~25 s) |
| `stop [--force] [--all]` | graceful `quitToDesktop`, kill after 20 s; `--all` also stops the lab server |
| `join [--user U --password P] [--ip IP --port N] [--keep-chat] [--keep-weather]` | from the main menu of a running client: join a server (default: the lab server, as its admin), drive character creation on a first join, wait until in the world (Multiplayer) |
| `server start [--mod-dir PATH]... [--mod NAME]... [--at X Y Z \| LOCATION] [--keep-world] [--no-wait]` | start the lab server (dedicated, loopback, own profile; `--at` = where new characters spawn); a fresh world unless `--keep-world`; ~45 s cold |
| `server stop [--force]`, `server status` | `quit` on its console (saves, exits), kill after 60 s / pid, address, heartbeat (`players`) |
| `server lua CODE` / `server lua -f FILE [--timeout MS]` | run Lua in the server's Lua state (the authoritative world) |
| `server cmd TEXT...` | server console command (`players`, `save`, `help`, `additem "user" Base.Axe`, ...) → its output lines |
| `server user NAME PASSWORD [--access admin]` | create the account if missing, set its access level |
| `server log [--errors \| --exceptions] [--all] [--tail N] [--grep RE]`, `server mark`, `server wait EXPR` | as `log`/`mark`/`wait`, for the server (its log is `<server profile>/stdout.log`) |
| `status` | pid, window, harness heartbeat (`state` menu/ingame, `age_s`, ticks, player) |
| `lua CODE` / `lua -f FILE [--timeout MS]` | run Lua in game; single expressions auto-`return`ed |
| `shot [--crop X Y W H] [--scale F] [--out P] [--check]` | in-game screenshot (frame buffer, UI included) → path; Read it to view. `--check` adds `problems` (uiState at the capture, e.g. `["pauseMenu", "paused"]`) |
| `shot --os` | PrintWindow capture — works with no/halted harness, menus, loading screens |
| `move X Y`, `click X Y [--button right] [--double] [--mod KEY]...`, `drag X1 Y1 X2 Y2 [--mod KEY]...`, `wheel X Y N` | mouse; `--mod alt` (shift, ctrl, ...; repeatable) holds keys around the click/drag |
| `hover OBJ [--allow-ui]` | mouse onto a world object (Lua expression or handle id) until the engine picks it → `{point (or null), candidates, tried, covered}`; points where UI blocks the world pick (`PZLab.pickBlockedBy`: click-through UI too) are skipped and listed in `covered` |
| `zoom [Z]` | camera zoom and the supported levels; with Z: snap to the nearest level, wait until settled → `{zoom, levels}` |
| `goto NAME` / `goto X Y [Z] [--radius R] [--anywhere]` | teleport to a catalogue location or tile (whole numbers: its centre), wait until the area is loaded → `{name, x, y, z, player, ms}`; multiplayer also `sent`, `server` (`lab.goto`). Off-map targets are refused unless `--anywhere` |
| `locations` | the test-location catalogue (`labkit/locations.json`) |
| `panes [--no-pin]` | `PZLab.showInventoryPanes`: vanilla inventory + loot windows visible, expanded, pinned → handles and pane info |
| `layout save` / `layout restore ID [--hide-added]` | snapshot the top-level UI windows → `{id, windows}` / put them back (`PZLab.restoreLayout`) |
| `wait EXPR [--timeout S] [--poll S]` | poll a Lua expression until truthy (e.g. `wait "not PZLab.playerBusy()"`) |
| `drag-row SRC ROW DST [--mod KEY]...` | drag row ROW of inventory pane SRC onto pane DST (handles from `PZLab.inventoryPanes()`) |
| `key NAME`, `keydown/keyup NAME`, `chord ctrl a`, `type TEXT` | keyboard (names in labkit/inputs.py KEYS) |
| `log [--errors \| --exceptions] [--all] [--tail N] [--grep RE]`, `mark` | console.txt since last mark; `--errors` drops known vanilla noise; `--exceptions` = JSON, one entry per engine-logged exception (`lab.log.exceptions`) |
| `reload FILE` | hot-reload a mod Lua file by its source path (resolved through the profile's mod links) |
| `load-lua NAME (SRC \| --code CODE) [--hot \| --boot]`, `load-lua --clear` | run Lua source as the file NAME through the engine's file loader (`lab.load_lua`); `--clear` deletes those files |
| `ui [--menus]` | UI state (`PZLab.uiState()`): pause menu, context menus, modal dialogs, speed, mouse-over UI (what a click hits; what blocks the world pick), text focus, drag, what Escape would do, `problems`/`playable`; `--menus` adds the open context menus with each option's click point |
| `playable [--speed N]` | `PZLab.ensurePlayable`: leave the pause menu (vanilla Resume path), close context menus, unfocus a text entry, set speed (0-4, default 1) → `{before, after, closed}`. Reports modal dialogs, never answers them |
| `run SCRIPT.py` | Python test script with `lab` (labkit.Lab) predefined; see the probes in scripts/: `probe_aim` (hover every container nearby; a click-through panel over one, without and with a consuming / click-through child: clicks vs picking), `probe_fixtures`, `probe_ui_state`, `probe_camera` (zoom levels × pickers × hover, UI-covered hover), `probe_layout` (snapshot/restore, inventory panes), `probe_locations` (re-verify the catalogue), `probe_engine` (re-verify the Lua engine facts: hot reload and events, Kahlua library, pcall'd errors, UI queues, timers), `probe_mp` (multiplayer: lab server + client; checks: goto to the player's own tile, beside a server-spawned vehicle, refusals of an indoor vehicle spot and an off-map target, then an inventory transfer; findings: a crate with a bag next to the player made on the server, vanilla transfers out of / into the bag and the crate, where the server says each item went and how the client's actions finished; starts and stops what isn't running) |

Python API (`from labkit import Lab`): `lab.start/stop/status/launch_world/wait_state`,
`lab.lua(code)` (raises LuaError), `lab.bridge.exec(code)` (raw result), `lab.input.move/click/
drag/wheel/tap/chord/type_text/press_modifiers/release_modifiers`, `lab.screenshot/os_screenshot`,
`lab.log.mark/lines/errors/exceptions`, `lab.reload(file)`, and:

| Python | Does |
|---|---|
| `lab.load_lua(name, source, hot=None)` | runs `source` as the file `name` (".lua" added; "/" subfolders) through the engine's file loader, from `<profile>/lua-scratch/` (under the cachedir, so `reloadLuaFile` accepts it): real file semantics, i.e. its file name in prototypes and stack traces, and the engine's hot reload when loaded again under that name. `hot=None`: the first load of a name in this game session is boot-like (as the engine loads mod files at boot: `Events.X.Add` registers), later ones are hot reloads (`reloadLuaFile`: see "Hot reload and events"); `hot=True` always reloadLuaFile, `hot=False` always boot-like. Source that doesn't compile raises LuaError and loads nothing. Runtime errors don't raise → `{name, path, hot, loads, value (chunk's first return value), errors (log.exceptions during the load)[, error]}`. Afterwards it runs the empty `_pzlab_bridge.lua`, so bridge chunks are no longer attributed to the file and event rewriting is off. `lab.lua_file_path(name)`, `lab.clear_lua_files()` (also done by `start`) |
| `lab.log.exceptions(since=None)` | one dict per engine-logged "Exception thrown" block since the mark/offset (`lab.log.size()` before the step): `{where, frame, exception, message, at, function, file, line, stack[, causes]}`; `file`/`line`/`function` from the top Lua frame, `stack` = up to 8 Lua frames (else Java frames). Errors caught by pcall are included (see traps). Assert a step logged nothing unexpected: `off = lab.log.size()` … `assert not lab.log.exceptions(since=off)` |
| `lab.input.click(x, y, button="left", double=False, modifiers=())` | `modifiers=("alt",)`, `("ctrl", "shift")`: keys held from a frame before the press until after the release |
| `lab.input.drag(x1, y1, x2, y2, button="left", steps=10, modifiers=())` | same, around the whole drag |
| `lab.hover(obj_expr_or_handle, allow_ui=False)` | mouse onto a tile object, floor item, corpse or vehicle until `PZLab.isHovered` confirms the engine resolves it → `{x, y}` or None. Aim points under visible UI are skipped (`PZLab.pickBlockedBy`: the engine picks nothing under any visible top-level element, click-through ones included); `allow_ui=True` tries them (only corpse/vehicle pickers can pass there). The point gets `covered` (every skipped point) when some were skipped; full report in `lab.last_hover` = `{point, candidates, tried, covered: [{x, y, ui, clickThrough}]}` |
| `lab.wait_until(lua_expr, timeout_s=10, poll_s=0.1)` | polls until Lua-truthy (0 and "" count as true) → the value; TimeoutError with the last value |
| `lab.inventory_panes()`, `lab.pane_rows(pane)` | `PZLab.inventoryPanes()` / `PZLab.paneRows(handle)` as lists |
| `lab.drag_row(src_pane, row_index, dst_pane, modifiers=())` | real-mouse drag of a pane row onto another pane; first raises if another element covers the row or the drop point (`PZLab.coveredBy`; the message names it, e.g. `ISCollapsableWindow "Foo" (handle 12)`), then waits for the game to acknowledge each phase (row under the mouse, drag started, destination hovered, drop handled; a failed phase names what now covers the point) → `{src, dst, row, from, to, modifiers, actions}` (`actions` = `PZLab.actionQueue()` right after the drop) |
| `lab.clear_fixtures()` | `PZLab.clearFixtures()` |
| `lab.ui_state()` | `PZLab.uiState()` (see UI state below; `modals`/`problems` always lists) |
| `lab.ensure_playable(speed=1)` | `PZLab.ensurePlayable{speed}` → `{before, after, closed: {pauseMenu, contextMenus, textEntry}[, errors]}` |
| `lab.close_context_menus()`, `lab.close_pause_menu()`, `lab.context_menus()` | the Lua functions of the same names |
| `lab.require_playable(allow=())` | raises `labkit.NotPlayable` (`.problems`, `.state`) unless every uiState problem is in `allow` (exact, or a prefix such as `"modal"`); returns the state. Call before acting so a stray pause menu or dialog can't silently void a test |
| `lab.screenshot(..., check=True)` | → `{path, problems}` instead of the path |
| `lab.set_zoom(z)`, `lab.get_zoom()`, `lab.zoom_levels()` | `PZLab.setZoom/getZoom/zoomLevels`: set_zoom snaps to the nearest level and returns the zoom once settled |
| `lab.goto(name_or_xyz, radius=10, timeout_s=30, anywhere=False)` | `PZLab.gotoLocation`: teleport to a catalogue name or `(x, y[, z])`, wait until loaded → `{name, x, y, z, player, ms}`. Multiplayer: `sent` (false: already standing at the point, nothing sent) and, on the lab server, `server` = the server's position of this player, waited for until it matches the client's (TimeoutError otherwise) |
| `lab.locations()` | the catalogue: name → entry (see Test locations) |
| `lab.show_inventory_panes(player=0, pin=True, inventory=None, loot=None)` | `PZLab.showInventoryPanes`, then waits until both panes show their list → `{inventory: {window, pane, rect, info}, loot: {...}}` (`info` = `paneInfo`); `inventory`/`loot` = `{x, y, w, h}` places the window |
| `with lab.ui_layout(hide_added=False) as snap:` | snapshot the UI layout, restore it when the block ends (also on errors). Also `lab.save_layout()` → snapshot, `lab.restore_layout(snap_or_id, hide_added=False)` → `{restored, changed, added, missing}` |
| `lab.start(..., join=True)` | multiplayer: lab server started if not running (same `enable`/`mod_dirs`/`world`), client to the menu, `lab.join()`; `join={"user": U, "password": P}` joins with that account → result plus `server` (when started) and `join` |
| `lab.join(user=None, password=None, server_password="", ip=None, port=None, timeout_s=300, close_chat=True, clear_weather=True)` | join from the main menu (default: the lab server as its admin) and wait until in the world → `{user, ip, port, created (character creation driven), pressed (screens), events (timed join log), ms, state}`; raises RuntimeError with vanilla's reason + events on failure. Then applies `world.closeUI`, closes the chat window (`close_chat`) and clears fog/rain (`clear_weather`, `PZLab.clearWeather`) |
| `lab.server` | the lab server (`labkit.server.Server`): `start(enable=None, mod_dirs=None, world=None, fresh=None, wait=True, timeout_s=300)` → `{profile, active_mods, world, address, pid, archived, pruned, ready_s, state}`, `stop(force=False, timeout_s=60)`, `status()`, `pid()`, `lua(code)` (raises LuaError), `bridge.exec(code)`, `wait_until(expr)`, `command(text, timeout_s=10)` → output lines, `ensure_user(name, password, access="admin")`, `players()`, `clear_fixtures()`, `log` (a `Log`: `mark/lines/errors/exceptions/size`), `setup(...)` (files only) |
| `lab.client(profile)` | another lab client with its own profile folder (e.g. `"profile-2"`) for this lab's server → a `Lab`; `Lab(profile=..., server_profile=...)` sets both folders explicitly (CLI: env `PZLAB_PROFILE` / `PZLAB_SERVER_PROFILE`) |

Start every test from a known state: `lab.ensure_playable()` (then `lab.require_playable()` before
steps that need world input). Close menus with `lab.close_context_menus()`, never with `key esc`.
Tests that pin, move, resize, open or close windows run inside `with lab.ui_layout():` so the next
test (or the person) gets the UI back as it was.

Typical inventory test: `lab.goto("grocery")` (or another catalogue place) → `PZLab.spawnWorldItem(...)`
next to the player → `with lab.ui_layout():` `panes = lab.show_inventory_panes()` →
`lab.drag_row(panes["loot"]["pane"], row, panes["inventory"]["pane"])` →
`lab.wait_until("not PZLab.playerBusy()")` → assert where the item is (`PZLab.get(h):getContainer()`)
→ `lab.clear_fixtures()`.

Typical multiplayer test (`scripts/probe_mp.py` does all of it):

```python
lab.start(mod_dirs=[MOD], join=True)              # lab server (fresh world) + client joined as admin
user = lab.lua("return getSpecificPlayer(0):getUsername()")
lab.server.lua('PZLab.usePlayer("%s") return true' % user)
ids = lab.server.lua("""local p = PZLab.player()        -- fixtures on the server: sent to the clients
    local crate = PZLab.spawnContainer(nil, math.floor(p:getX()) + 1, math.floor(p:getY()), 0,
        { contents = { { "Base.Bag_Schoolbag", 1, contents = { "Base.Apple" } } } })
    local bag = crate:getContainer():getItems():get(0)
    return { bag = bag:getID(), apple = bag:getInventory():getItems():get(0):getID() }""")
lab.wait_until("PZLab.findItem(%d, { radius = 3 }) ~= nil" % ids["apple"])   # same ids on clients
lab.lua("PZLab.traceActions({ 'ISInventoryTransferAction' }) return true")
lab.lua("""local p = getSpecificPlayer(0)              -- the client acts through vanilla timed actions
    ISTimedActionQueue.add(ISInventoryTransferAction:new(p, PZLab.findItem(%d, { radius = 3 }),
        PZLab.findItem(%d, { radius = 3 }):getInventory(), p:getInventory()))""" % (ids["apple"], ids["bag"]))
lab.wait_until("not PZLab.playerBusy()", timeout_s=40)
print(lab.server.lua("return PZLab.placeText(PZLab.locate(%d))" % ids["apple"]))  # server truth
print(lab.lua("return PZLab.actionTrace()"))       # how the client's action went
lab.server.clear_fixtures(); lab.stop(); lab.server.stop()
```

Coordinates everywhere are game UI pixels = window client pixels (1280×720 by default).

## Layout

- Config layers (later wins): built-in defaults (`labkit/config.py`) → `lab.json` (portable, committed: resolution, world, game args) → `lab.local.json` (this machine, gitignored: game_dir, options_overrides) → env `PZLAB_GAME_DIR`, `PZLAB_PROFILE` (profile dir, relative to the repo root), `PZLAB_SERVER_PROFILE` → CLI. Keys: `mods` (`name: path`), `enabled`, `world` (map, x, y, z, sandbox, god, closeUI, onStart; also the lab server's world), `heap` (null = game's own), `keep_saves`, `window`, `server` (Multiplayer).
- `profile/` (gitignored; `profile*/` for alternates) — the game's `-cachedir`: options.ini, mods/ (links + default.txt), Lua/pzlab/ (bridge files), lua-scratch/ (`lab.load_lua` files, cleared by `start`), Saves/, Screenshots/, shots/, console.txt.
- `<profile>-server/` (`profile-server/`, `profile-agent-server/`, ...; gitignored) — the lab server's `-cachedir` (Multiplayer).
- `harness/PZLabHarness/` — in-game mod (always active in the lab, on the lab server too). `shared/` (client and server): PZLab_Core (log, files, JSON, handles, compiler reset), PZLab_Bridge (commands, heartbeat, steps), PZLab_Inspect (player, squares, describe objects, where an item is, item search and watch), PZLab_Fixtures (spawn/track/clear test objects, synced to clients when made on a server; world search). `client/`: PZLab_Boot (autostart world), PZLab_Join (joining a server unattended), PZLab_Tools (teleport, picking, Lua file loading, timed actions and their tracing, weather), PZLab_UI (UI tree, inventory panes, layout snapshots, input recorder), PZLab_UIState (uiState, pause/context menu recovery, ensurePlayable, speed), PZLab_World (zoom, locations, goto, building/open-ground search). `server/`: PZLab_Server (server-only helpers; returns early unless `isServer()`, since clients load `server/` too).
- `labkit/` — Python: config, detect (game dir, client and server launch specs, version facts read from the install), profile, process, bridge, inputs, capture, imaging/png (Pillow-optional), logs, lab, server (the lab server), locations (+ `locations.json`, the catalogue); `luacheck/` (precompiled `LuaCheck.class`, Java 17 bytecode, + source).

Isolation: `-cachedir` keeps everything in the profile; `-nosteam -nosound -novoip -debug`. The lab server likewise (its own `-cachedir`, `-nosteam`, bound to 127.0.0.1, UPnP off). Outside this repository the lab only *reads*: the game install, Steam's library list, and (in `init` only) two keys of `~/Zomboid/options.ini`. Saves are pruned to `keep_saves` (server worlds: archived, then pruned).

Version-sensitive facts are read from the installed game, not hardcoded: JVM args/classpath/main class from `ProjectZomboid64.json` (only the highest OS-version entry applies), the lab server's from the java line of `ProjectZomboidServer.bat` (`detect.server_launch_spec`), the mods reset-marker name from `GameWindow.class` (`resetDefaultModsForNewRelease("NN_NN")`), the sandbox file version from `SandboxOptions.SANDBOX_VERSION` (`detect.class_int_constant`). If a future build moves them, `doctor` and the stall report point at it.

## Bridge protocol (profile/Lua/pzlab/)

- Per command: `cmd-<seq>.txt` = `PZLAB <seq> <timeoutMs>\n<lua chunk>`, written and closed first, then the empty flag `cmd-<seq>.go`; the controller deletes both after the reply (flag first). `seq` is decimal and increasing.
- Harness polls every 50 ms from `OnFETick` (menu) / `OnTickEvenPaused` (in game): it *lists* the directory (no open) for flags newer than the last command run, opens that command file once, runs the chunk synchronously via `pcall`, writes `res.json` `{seq, ok, value|error, out, frames, ms}`. So it never opens a file the controller is writing, replacing or deleting (see traps). A fresh Lua state resumes after the `seq` in `res.json`.
- `locations.txt`: the catalogue as a Lua chunk, written at setup (`PZLab.locations`).
- Multi-tick: return `PZLab.steps{fn(ctx), n_frames, PZLab.waitMs(ms), PZLab.waitUntil(fn, timeoutMs[, describe]), ...}`; result = last function's return; on a timeout `describe(ctx)`'s text is added to the error. **No coroutines** (see traps).
- `state.json` heartbeat every 0.5 s (in game also `speed` and `pauseMenu`; connected to a server `mp: true`; while joining `connect`, see `PZLab.joinServer`). Stale heartbeat + live process = loading, "Click to Start", or Lua halted → `shot --os`.
- The lab server runs the same bridge in `<server profile>/Lua/pzlab/`, ticked by `OnTickEvenPaused` (fires on a dedicated server every loop, 10 per second, players or not, paused or not); its heartbeat has `state: "server"` and `players` (`{user, x, y, z, dead}`).
- `PZLab.out(...)` appends lines to the reply's `out`.
- Chunks are compiled with `loadstring(chunk, "pzlab")`: in stack traces the chunk is `pzlab`, attributed to the last file the engine ran (see traps). If compiling fails for a reason other than a syntax error (`pzlab:<line>: ...`), the bridge calls `PZLab.resetCompiler()` once and retries, noting it in `out`.
- Results: every Java object is `{"__java": class, "str", "handle"}` and every UI element table is a summary `{"__ui": Type, "handle", "title", "x", "y", "w", "h", "visible"}`; pass the handle back as `PZLab.get(h)`. An empty Lua table arrives as `{}`.

## Lua helpers (PZLab.*)

`player()`, `square(x,y,z)`, `teleport(x,y,z)` (whole numbers → tile centre, fractions exact; multiplayer: only sends the server's `/teleportto` and returns `{sent = true}` at once, `gotoLocation` waits), `onMap(x, y)` (the 8×8 chunk has map data: false off the authored map; the metagrid bounds, ±250 cells, and `isValidSquare` don't tell), `toScreen(x,y,z)`, `objectScreenPos(obj)`, `aimPoints(obj, n)` (on-screen points, best first; up to n clear of UI, then up to n where `pickBlockedBy` finds UI, marked `ui = <type>`, `clickThrough`), `isHovered(obj [,x,y [,allowUI]])` (false where `pickBlockedBy` finds UI unless allowUI), `pickCorpse(x,y)` / `pickVehicle(x,y)` (both UI pixels; see traps), `describeObject(obj)`, `describeContainer(c)`, `containersOf(obj)`, `objectsAt(x,y,z)`, `findContainers(radius [, {near = {x,y,z} | player = user}])` (with `handle`), `ui(includeHidden)`, `closeUI(type)`, `mouse()` (pos + engine-picked object), `give(fullType, n)`, `addToContainer(c, fullType, n)` (untracked), `screenshot(name)`, `launchWorld(cfg)`, `clearWeather()` (admin overrides fog and precipitation 0, as the admin panel's climate controls; sent to the server in MP: needs admin), `json(v)`, `log(msg)`, `readFile/writeFile(name)`.

**Where things are** (PZLab_Inspect; client, server and single player alike).

| Lua | Does |
|---|---|
| `player(name)` | client: local player 0; server: the connected player `name`, else `PZLab.serverPlayerName`'s (`usePlayer(name)` on the server sets it), else the first connected one (nil when none) |
| `players()` | `{user, x, y, z, local, handle}` per connected player (server: all; client: itself and the remote players it knows) |
| `whereIs(item or handle)` | as this side sees it: `{id, type, on = "floor" \| "container" \| "nowhere", path}`; `path` innermost holder first: `{kind = "item", type, id, container}` (a bag), `{kind = "character", user, container}`, `{kind = "object", sprite, index, at, container}` (furniture, crate), `{kind = "corpse", at}`, `{kind = "vehicle", part, vehicle, vehicleId}`, `{kind = "floor", at, x, y, z}` |
| `placeText(where)` | one line: `"item:Base.Bag_Schoolbag < object:carpentry_01_19"`, `"character:admin"`, `"floor"`, `"nowhere"` |
| `findItem(id, {near = {x,y,z} \| player = user, radius = 8, zMin, zMax})` | the item with that `InventoryItem:getID()` (identical on server and clients) in connected players' inventories, then in tile containers, floor items (and their contents), corpses and vehicles around the centre (recursive) → item or nil |
| `locate(id, opts)` | `whereIs(findItem(id, opts))` plus `handle`, or `{id, on = "nowhere"}` |
| `watchItems(ids, opts)` / `itemWatch(stop)` | from now on, every tick, `placeText` of each item; changes recorded with `getTimestampMs()` (the same clock in a server and a client on one machine) → `{[id] = {place, changes = {{t, from, to}}}}`; `stop` ends it. On the server: when the authoritative location changed |

**Lua files and the compiler** (behind `lab.load_lua`; paths absolute, `/`-separated, under an allowed prefix: see traps).

| Lua | Does |
|---|---|
| `reload(path)` | vanilla `reloadLuaFile(path)` → the chunk's first return value (nil on failure; failures are only logged) |
| `loadLua(path, source, hot, afterPath)` | `hot = true`: `reloadLuaFile(path)`. `hot = false`: boot-like load; the file at path must be empty: reloading it only points the compiler at path, then `source` is compiled and run (prototypes get path, as RunLua gives them; no event rewriting). `afterPath`: an empty file run last. Counts loads per path in `luaFileLoads` → `{path, hot, loads, value, error}` |
| `luaFileInfo(path, source)` | `{loads, compileError}`: compiles with `loadstring`, which logs nothing |
| `eventsRewriting()` | true while `Events.X.Add/Remove` are no-ops (`LuaCompiler.rewriteEvents` on); fires a private event `PZLabEventProbe` |
| `resetCompiler()` | reloads `PZLab_Core.lua` (reload-safe, no events), which turns stuck event rewriting off |

**Handles.** `handle(v)` → small int id (same object → same id; keys compare with Java `equals`), `get(id)`, `clearHandles()`.

**Fixtures** (lab setup: created directly, tracked, removed by `clearFixtures()` wherever they have moved to — floor, any container, hands). Item spec: list of `"Base.Apple"` | `{"Base.Apple", 3}` | `{"Base.Bag_Schoolbag", 1, contents = {spec}}`. In multiplayer, make them on the server (`lab.server.lua(...)`): every creation and removal there is sent to the clients; on an MP client they raise an error, because objects made there exist only in that client (Multiplayer).

| Lua | Does |
|---|---|
| `addItems(target, spec)` | create items in a container → top-level InventoryItems. target: ItemContainer, container item, character, tile object/corpse, VehiclePart, floor item, or handle |
| `spawnWorldItem(fullType, x, y, z, {offX, offY, offZ, contents})` | item on the floor (offsets within the tile, default 0.5, 0.5, 0) → IsoWorldInventoryObject |
| `spawnCorpse(x, y, z, {female, clothed, contents, blood})` | vanilla `createRandomDeadBody`; unless `clothed` its outfit is stripped, so the container (type `inventorymale`/`inventoryfemale`) holds exactly `contents` → IsoDeadBody |
| `spawnVehicle(script, x, y, z, dir, {loot, contents = {[partId] = spec}, open, key})` | vanilla `addVehicleDebug` (default `"Base.CarNormal"`, dir `"E"`), centred on the square's NW corner; containers emptied unless `loot`; `open` unlocks/opens doors and trunk; `key` gives the key; the key vanilla drops nearby is removed at once (traps) → BaseVehicle. Raises where vanilla won't place one (indoors without a vehicle zone, overlapping a vehicle: traps) |
| `vehiclePlaced(vehicle)` | the vehicle is really in the world: its physics transform matches its x/y |
| `spawnContainer(sprite, x, y, z, {contents, name})` | a world container: a new tile object from a sprite with a `container` property (default `"carpentry_01_19"`, the player-built wooden crate, type `crate`), `contents` inside → IsoObject (`obj:getContainer()`) |
| `newItem(fullType, contents)` | a tracked item, contents inside, in no container yet |
| `vehicleContainers(vehicle)` | `{part, index, type, items, area, world = {x, y}, center = {x, y} (screen), accessible, handle}` per part container |
| `openVehicleDoors(vehicle, open)` | unlock + open (or close) every door but the hood |
| `clearFixtures()` / `fixtures()` | remove all tracked fixtures still in the world (newest first) → count / list them (`{kind, handle, str, loaded}`). Fixtures whose square isn't live are skipped and stay listed (`loaded = false`): outside the loaded chunks around the player, or on a server further than `PZLab.SERVER_FIXTURE_REACH` (50) tiles from every connected player (traps). After their chunk loads again they are found by item id / a `modData.pzlabFixture` marker |
| `findObjects({radius=6, zMin, zMax, hasContainer, sprite="pattern", containerType, class="IsoDeadBody", limit, near = {x,y,z}, player = user})` | tile objects, floor items, corpses and vehicles around the player (or `near`) → `describeObject` + `handle`, `dist`, nearest first |
| `playerBusy()` / `actionQueue()` | client: timed actions queued or running (vanilla queue + `isPlayerDoingAction`) / `{busy, queued = {Type...}}` |
| `traceActions(classNames)` / `traceAction(action, queued)` / `actionTrace(clear)` | client: wraps the classes' `waitToStart/start/update/perform/stop/forceComplete/forceStop` (default `{"ISInventoryTransferAction"}`; `false` restores them) and records per action: `{n, type, item = {type, id}, src, dest, queued, first, started, ended (ms since tracing began, PZLab.actionTraceStart = its getTimestampMs()), result = "performed" \| "stopped" \| nil (running), waits, updates, maxTime, events = {{name, t, maxTime}}}`; `traceAction(a, true)` before `ISTimedActionQueue.add(a)` records the queue time and returns the record (`n`). In MP an update's maxTime going -1 → n is the server's duration reply |

**UI.**

| Lua | Does |
|---|---|
| `findUI(typeOrPredicate, {visibleOnly=true, root, exact, limit})` | recursive search of top-level elements and their children (Java `getControls`); a type name matches derived Lua classes too (`isA`), or a Java class name for Java-only UI → Lua tables (Java objects when no table) |
| `isA(t, typeName)`, `uiElement(handle)`, `uiRoot(el)` | class check through the metatable chain / handle → table / top-level window |
| `rect(el)`, `visibleRect(el)` | absolute `{x, y, w, h}` / the part actually drawn: clipped by ancestors' `maxDrawHeight` (collapsed windows) and the screen |
| `inventoryPanes()`, `paneInfo(pane)` | every visible `ISInventoryPane` (derived classes too, any host window): `{handle, rect, listRect, shown, containerType, container, items, rows, mode, owner = {type, title, name, handle, collapsed}, drop = {x, y}}` |
| `paneRows(pane)` | on-screen rows: `{index, rect, point, partial, name, fullType, count, isGroup, collapsed, child, equipped, isContainerItem, selected, hovered, handle}`. Hover/click/drag at `point` (name column): vanilla sets `mouseOverOption` only left of `column3` |
| `showInventoryPanes({player = 0, pin = true, inventory = {x, y, w, h}, loot = {...}})` | the player's vanilla inventory and loot windows back in the UI, visible, expanded, pinned (unless `pin = false`), placed if asked, kept on screen, brought to front → `inventoryPanesOf(player)`; pane geometry is final a frame later (`lab.show_inventory_panes` waits) |
| `inventoryPanesOf(player)` | `{inventory = {window, pane, rect, info = paneInfo}, loot = {...}}` |
| `openInventoryWindows(playerNum)` | older form of `showInventoryPanes` → `{inventory pane, loot pane}` handles |
| `saveLayout()` | snapshot of every top-level element: `{id, windows = {{handle, type, title, name, x, y, w, h, visible, attached, pinned, collapsed, maxDrawHeight}}}` (`pinned`/`collapsed` for windows that have them, `maxDrawHeight` otherwise); kept in the harness under `id` |
| `restoreLayout(snapshotOrId, {hideAdded})` | re-adds removed elements, restores size, position, pin (vanilla pin/collapse handlers), collapsed and visible → `{restored, changed = {{type, title, fields}}, added, missing}`; `hideAdded` hides windows opened since. Z-order is not restored. Handles must survive (no `clearHandles()` in between) |
| `recordInput(on)`, `inputEvents()` | per-tick log of mouse button presses/releases with `alt/shift/ctrl` as the game saw them |

**Camera and places** (PZLab_World).

| Lua | Does |
|---|---|
| `getZoom()`, `zoomLevels()` | `getCore():getZoom(0)` (> 1 shows more world) / supported levels, closest first (options `zoomLevels2x`/`1x`, empty = all of 0.25..2.5; 1 always) |
| `setZoom(z, timeoutMs)` | snaps to the nearest level, steps the target there like the mouse wheel (`Core.doZoomScroll`), waits until settled. A steps job: `return PZLab.setZoom(1)` → final zoom. Errors if auto zoom or zoom is off in the options |
| `locations`, `loadLocations()` | the catalogue (from `pzlab/locations.txt`) |
| `gotoLocation(nameOr{x,y,z}, {radius = 10, timeoutMs, settleFrames = 10, anywhere})` | teleport (whole numbers: the tile centre), wait until `areaLoaded`, re-place the player if it dropped, settle, wait until on the target tile → `{name, x, y, z, player, ms}`; timeouts say where the player is. Refuses a target off the map (`onMap`) unless `anywhere`. A steps job. Multiplayer: only the server moves the player: vanilla admin `/teleportto x,y,z` with the exact point (fractions; needs admin), nothing sent when the player already stands within `PZLab.GOTO_ARRIVE` (0.25) of it (`sent = false`); arrived = the client stands within 0.25 of the point; the client never sets its own position (traps). `lab.goto` then waits for the server's copy |
| `areaLoaded(x, y, z, radius)` | every ground-floor square within radius exists, and the target square |
| `findBuildings({near = {x, y}, radius = 300, levels, rooms = {...}, anyRooms = {...}, limit = 10})` | buildings anywhere on the map from the metagrid (unloaded too): `levels = 2` means rooms on two floors ≥ 0; `rooms` all present, `anyRooms` one present (map room names: kitchen, bedroom, grocery, gigamart, warehouse, ...) → `{x, y, x2, y2, center, dist, floors = {{z, rooms}}}` nearest first. ~170 ms for the whole map |
| `findOpenArea(w, h, {radius = 30, z = 0, near})` | nearest loaded w×h rectangle of open outdoor ground (only floor, grass, ground decals: `PZLab.openGroundSprites`; no buildings, walls, fences, trees, furniture, corpses, vehicles) → `{x, y, w, h, center, dist}` or nil |
| `isOpenGround(x, y, z)` | the per-square test behind `findOpenArea` |

To find containers on an upper floor: `findBuildings({levels = 2, rooms = {"bedroom"}})` → `gotoLocation({x, y, 1})` inside it → `findObjects({hasContainer = true, zMin = 1, zMax = 1})`.

**Test locations** (`labkit/locations.json`, vanilla map "Muldraugh, KY", verified on 42.21.0; `lab.goto(name)`, `goto NAME`, `start --at NAME`; each entry has `area`, `rooms`, `containers` per floor and `contents`; `scripts/probe_locations.py` re-checks them after a game update):

| Name | Tile | What is there |
|---|---|---|
| `spawn-house` | 6119, 5474, 0 | lab.json's default start: single-storey Riverside house, 34 containers (kitchen, bedrooms, office) |
| `two-storey-house` | 6079, 5427, 0 | kitchen of a two-storey house: 22 containers downstairs, 16 upstairs; stairs 6080,5429-5431 |
| `two-storey-house-upstairs` | 6081, 5427, 1 | its upstairs hall (bedrooms, bathrooms, closet); 10-12 of 16 upstairs containers hoverable from here |
| `grocery` | 6136, 5370, 0 | grocery with 44 containers: 17 shelves, fridges, display cases, counters |
| `parking-lot` | 6148, 5384, 0 | open asphalt, 12 × 16 tiles (6142..6153, 5376..5391) for vehicles, corpses, floor items |
| `gigamart` | 6505, 5345, 0 | supermarket: 227 containers (62 shelves, 27 metal shelves, 33 grocer stands, 28 fridge and 27 freezer compartments), offices upstairs (35) |
| `warehouse` | 5614, 5975, 0 | 194 crates; unlit inside, so screenshots are black (picking works) |

**UI state and recovery.** Modal dialogs are only reported: accepting or declining one (Terms of
Service, overwrite/delete prompts) is a decision for the test or its person, never automatic.

| Lua | Does |
|---|---|
| `uiState()` | `{ingame, mainMenu, pauseMenu, contextMenus (visible menus incl. submenus), modals, speed (0 paused, 1, 2-4 fast), multiplier, paused (isGamePaused), mouse = {x, y}, mouseOverUI (`uiAt()`: what a click at the mouse hits), pickBlockedBy (`pickBlockedBy()`: what keeps the world from being picked at the mouse), focus, drag (false / "items" / "placement"), escape, problems, playable}`. `problems`: `notInGame`, `pauseMenu`, `paused`, `contextMenus`, `modal:<Type>`, `textEntry`, `drag:items`, `drag:placement`; `playable` = none |
| `ensurePlayable({speed = 1})` | `closePauseMenu`, `closeContextMenus`, `Core.UnfocusActiveTextEntryBox()` if a text entry has focus (what any left click does), `setSpeed` → `{before, after, closed = {pauseMenu, contextMenus, textEntry}, errors}`. Leaves modals and drags alone |
| `closeContextMenus()` | each player's menu via vanilla `ISContextMenu:closeAll()` (submenus and option tooltips too), any other visible `ISContextMenu` via `hideAndChildren()` → count closed. No key events |
| `closePauseMenu()` / `isPauseMenuOpen()` | the in-game Escape menu's Resume item (`returnOption:onMouseDown` → `MainScreen.onMenuItemMouseDownMainMenu` "RETURN" → `ToggleEscapeMenu`): restores the UI it hid, speed 1 → true; false when not open; raises if it stays open (options confirm dialog) |
| `setSpeed(n)` | like vanilla's speed keys: `setGameSpeed(n)` plus time multiplier 1/5/20/40 for n = 1..4; 0 pauses |
| `contextMenus()` | visible menus back to front: `{handle, player, sub, rect, options = {{index, name, disabled, subMenu, point}}}`; hover/click `point` (final position; vanilla may slide a new menu in) |
| `modals()`, `modalTypes` | visible modal dialogs, front first: `{type, title, name, text, handle, buttons = {{title, internal, enabled, point}}}`. Types in `PZLab.modalTypes` (extend for a mod's dialogs) or containing `Modal`/`Dialog`; Java `UIManager.getModal()` as `ModalDialog` |
| `uiAt(x, y)` | **clicks**: front-most visible top-level element that would take a click there instead of the world: capturing the mouse, or under the point with a subtree that consumes the press there (a visible child under the point that consumes, else the element's own `consumeMouseEvents`; mouse routing in traps) → `{type, title, name, handle, capture[, child]}` or false; `child` = the descendant that takes it when not the element itself (e.g. a consuming child of a click-through panel). Assumes Lua `onMouseDown` handlers return nil or `isWantMouseEvents()` as vanilla `ISPanel` does; AtomUI elements are assumed to take the click (traps). `mouseOverUI` = `uiAt()` at the mouse |
| `pickBlockedBy(x, y)` | **hovering**: front-most visible top-level element under the point, which keeps the engine from picking the world there (`getLastPicked()` nil: no hover, no object tooltip; traps). Children and mouse-event consumption don't matter → `{type, title, name, handle, clickThrough}` or false; `clickThrough` = a press there passes this element and its children, so `uiAt` may be false there. `isHovered`, `aimPoints`, `lab.hover` use it. Both helpers: the top-level element's own hit test (`isOverElement`: UIElement = its rectangle, children not included; AtomUI: child nodes too) clipped to `maxDrawHeight`, object tooltip skipped, coordinates floored; default point the mouse; they describe the UI as it is now (an element that moves away from the cursor is gone once the mouse gets there) |
| `coveredBy(el, x, y)` | false when a click at (x, y) reaches el's own top-level window; else `uiAt`'s description of what is in front, or `{type = "world"}` when no UI takes it |
| `textFocus()` | text entry holding keyboard focus → `{type, handle, text, root = {type, title, handle}}`, `{type = "unknown", detached = true}` when its window was removed, or false |
| `escapeAction()` | what an Escape press would do now: `textEntry`, `consumed:<Type>` (a window's `isKeyConsumed`), `cancelPlacement`, `openPauseMenu`, `closePauseMenu`, `menu` (main menu), `none` |

## Multiplayer (lab server + clients)

**Setup**: nothing beyond "New machine": the lab server comes from the installed game (no Steam,
no dedicated-server download, no Terms of Service screen), each client profile carries the person's
ToS acceptance like any lab profile. `python pzlab.py start --join --mod-dir <mod>` = server + one
client in the world; `python pzlab.py stop --all` ends both.

**The lab server** (`labkit/server.py`): `zombie.network.GameServer` with the JVM args, classpath and
main class of the install's own server launcher (`ProjectZomboidServer.bat` java line; a
dedicated-server install's `ProjectZomboid64.json`; else the client's spec), `-Dzomboid.steam=0
-nosteam -cachedir=<server profile> -servername <name> -adminusername/-adminpassword (no
first-run password prompt) -ip <ip> -port <port> -udpport <udp_port>`, `-Ddebug` if `server.debug`.
stdin is `<server profile>/pzlab-console.txt`: the server polls stdin every 100 ms, so `command()`
appends a line and reads the reply from stdout (the server echoes `command entered via server
console (System.in): "..."` first). Ready = `*** SERVER STARTED ****` in its log plus a fresh
harness heartbeat; ~45 s cold (first start of a profile ~70 s: world generation).

| `server` key (lab.json) | Default | |
|---|---|---|
| `profile_dir` | `<profile_dir>-server` | its `-cachedir` (env `PZLAB_SERVER_PROFILE`) |
| `name` | `pzlab` | `Server/<name>.ini`, `Saves/Multiplayer/<name>`, `db/<name>.db` |
| `ip`, `port`, `udp_port` | `127.0.0.1`, 16361, 16362 | bind address (loopback: unreachable from the network), ports (vanilla's 16261 left free) |
| `admin_user`, `admin_password` | `admin`, `pzlab` | created/updated at every start; `join` uses it by default |
| `fresh` | true | each start moves the world and `db/<name>.db` (accounts) to `Saves/Multiplayer/<name>_<time>/` (newest `keep_saves` kept): a new world, every client creates a character; `server start --keep-world` / `fresh=False` keeps them |
| `options` | `{}` | `Server/<name>.ini` values on top of the lab's |
| `heap`, `debug`, `args` | null, false, [] | -Xmx, -Ddebug, extra server arguments |

Files written at each start: `Server/<name>.ini` (only these keys are set, the server fills in the
rest: `Mods=PZLabHarness;<the client's enabled mods>`, `Map=<world.map>`, `DefaultPort`, `UDPPort`,
`SpawnPoint=<world x,y,z>`, `UPnP=false` (vanilla default true maps the port on the router!),
`Public=false`, `SteamVAC=false`, `SteamScoreboard=false`, `VoiceEnable=false`, no backups,
`ServerWelcomeMessage=`, `Open=true`, `Password=`, `RCONPassword=`), `<name>_SandboxVars.lua`
(`world.sandbox` overrides; the server keeps defaults for the rest; `VERSION` read from
`SandboxOptions.SANDBOX_VERSION` in the jar, without it the loader "upgrades" from version 0),
`<name>_spawnregions.lua` + `<name>_spawnpoints.lua` (one region, one point at world x, y, z: the
client skips the spawn-region screen and every new character spawns there). Mods are linked into
`<server profile>/mods/` like the client's; the server also loads PZLabHarness (it must: a joining
client reloads its Lua with the server's mod list).

**Joining** (`lab.join`, client/PZLab_Join.lua; ~35-50 s from the menu): vanilla Direct Connect
(`MainScreen:presentServerConnectPopup`, fields, Connect button). Right after connecting the client
resets its Lua state and loads the server's mods; the request lives on in `pzlab/join.txt` and the
fresh harness resumes. Then character creation: the profession screen's and the appearance screen's
Next/Play buttons are pressed (`ISButton:forceClick`, never a disabled one; the spawn-region list
too if a server offers several regions), then loading and "Click to Start" (dismissed like a world
start). A returning character loads straight in. The join report has the timed events. Accounts:
a client started with `-debug` (lab default) is refused (`UI_OnConnectFailed_DebugNotAllowed`;
bytecode, `ConnectToServerState`) unless its role has `ConnectWithDebug`: use the admin account, or
`server user NAME PW` (access admin) for more players. Every connection needs its own account
(`AlreadyConnected` otherwise; bytecode, `LoginPacket`). Verified failures: nothing listening →
"Connection Failed" after ~7 s; wrong password → "Wrong username or password given" after ~0.4 s;
a later `join` from the same client works (the failed connect screen is left first).

**More clients**: `other = lab.client("profile-2")`, `lab.server.ensure_user("p2", "pw")`,
`other.start(join={"user": "p2", "password": "pw"})` (CLI: `PZLAB_PROFILE=profile-2
PZLAB_SERVER_PROFILE=<server profile> python pzlab.py start --join --user p2 --password pw`). Each
is its own game process and window (~4 GB each; the server ~3 GB); input goes to each client's own
window. Verified with two clients (both on the server's player list and the first client's).

**Server-side Lua** (`server lua`, `lab.server.lua`): the shared harness (Core, Bridge, Inspect,
Fixtures) plus server/PZLab_Server.lua: `usePlayer(user)`, `serverInfo()` → `{name, players,
tickEvent, worldAgeHours, sandbox = {Zombies}}`. Differences from a client: no UI, no
`getSpecificPlayer` (→ `PZLab.player(name)`), `getPlayerFromUsername` returns nil (42.21;
`PZLab.player` searches `getOnlinePlayers()`), 10 ticks per second (`GameServer.FPS`): a command
answers within ~100 ms, a `PZLab.steps` entry takes a tick, `waitMs` resolution 100 ms. Only the
chunks around connected players are live. `reloadLuaFile` works there (`reloadLuaFile(
getFilenameOfClosure(PZLab.someFunction))`; `lab.reload` and `lab.load_lua` are client-only).
Console commands that act on a player take a username (`additem "admin" Base.Axe 2`,
`teleportto`, `godmodplayer`, ...; `help` lists them); an admin client can send the same with
`SendCommandToServer("/additem ...")`.

**Server truth vs client view**: item ids (`getID()`) are the same on server and clients;
`PZLab.locate(id)` / `whereIs` / `placeText` on either side, `watchItems` on the server for when
the authoritative location changed, `traceActions` on the client for what its action did.

**Fixtures in MP** (verified): made on an MP client (`PZLab.allowClientFixtures = true` to allow)
they exist only there: the server finds nothing (`locate` → nowhere) and rejects a transfer of
such an item (~30 ms). Made on the server they reach every client with the same ids: items in
containers (`sendAddItemToContainer`), floor items (`AddWorldInventoryItem` transmits, contents
included), world containers (`transmitCompleteItemToClients` of the empty object, then each item),
corpses (`createRandomDeadBody` alone stays server-side; `sendCorpse`), vehicles (server
authoritative; part contents and the key too). Removals (`clearFixtures` on the server) reach the
clients (`sendRemoveItemFromContainer`, `transmitRemoveItemFromSquare`, `removeCorpse`,
`permanentlyRemove`).

**Item transfers in MP** (`scripts/probe_mp.py`, 42.21, admin account, verified with the lab crate
and with a vanilla counter): an `ISInventoryTransferAction` asks the server for an item transaction
in `start()` (`createItemTransaction`; `maxTime` -1), the server replies with a duration within
~50 ms (`maxTime` = 5-50), the action ends with `forceComplete` (done packet) or `forceStop`
(rejected), and vanilla then clears the rest of the queue after a stop.

| Transfer | Server | Client action |
|---|---|---|
| item in a bag in a world container → inventory | accepted (duration 50), never done, nothing sent: the item stays in the bag | runs until the client drops the transaction (duration + 10 s, `TransactionManager` `TRANSACTION_TIMEOUT_BUFFER_MS`), which `isItemTransactionDone` reports as done: `forceComplete`, `perform` after ~11 s, as if it succeeded |
| inventory → a bag in a world container | rejected | `forceStop` after ~35-45 ms |
| world container → inventory, inventory → world container, bag on the floor → inventory | moved ~200-310 ms after the action started | `perform` after ~130-230 ms |

## Engine facts and traps (verified on 42.21)

- **Mods load at boot from `mods/default.txt`** — but if `mods/reset-mods-<NN_NN>.txt` (42_00 in 42.21) is missing the game creates it and empties default.txt. Setup writes the marker (name read from the jar).
- **First launch in a fresh profile shows the Terms of Service** (`termsOfServiceVersion` in options.ini). Human acceptance only — see "New machine".
- **`-debug` + a Lua error opens the Lua debugger and halts all Lua** (harness included), **even an error caught by `pcall`** ("Break On Error" = `UIManager.setShowLuaDebuggerOnError`). `GameWindow.enter()` turns it on right after `OnGameBoot`, i.e. after mod Lua has loaded, so the harness turns it off on its first tick (and at load). Harness probes therefore check classes (`instanceof(j, "UIElement")`) rather than pcall methods that may not exist. If halted anyway: `shot --os`, then press and hold ~0.2 s (`lab.input.down`/`up`) on Resume ≈ (560, 21): the debugger polls input slowly and misses a quick click. Untick "Break On Error" (≈ 342, 21) first if it keeps breaking.
- **Coroutines crash Kahlua**: a coroutine finishing inside `pcall` from an event handler → NPE in `KahluaThread.luaMainloop` and the event stops firing. Vanilla never uses coroutines.
- **Hot reload and events (verified, `probe_engine`)**: `reloadLuaFile(path)` = `LuaManager.RunLua(path, true)`. While the file runs `LuaCompiler.rewriteEvents` is on, so `Events.X.Add/Remove` do nothing (`zombie.Lua.Event$Add/$Remove` return early; also on a file's *first* load through reloadLuaFile), and every LuaClosure created meanwhile calls `LuaEventManager.reroute`, which replaces each registered callback whose prototype has the same `filename` (the full path as loaded) and `name` with the new closure. A prototype's name is the compiler's current token string at the function body (`LexState.body` → `new FuncState(ls, t.ts)`): the last name or string before `function`. So `local function onTick()`, `function M.onTick()` and `local onTick = function()` are all `onTick`; every closure of one generic helper (`local handler = function(...)` inside a register function) is `handler`; every `Events.X.Add(function() ... end)` is `Add`. Observed after a reload: a named function's callback runs its new version; three callbacks registered through one helper (two OnTick, one OnPlayerUpdate) all run the helper's *last* new closure; two inline `Add(function ...)` callbacks both run the last inline one. Authors: register distinct named functions from the file that owns them, or one dispatcher per event registered outside reloads. Test it with `lab.load_lua`.
- **Nil prototype names**: where the parser had looked ahead the name is nil (stack traces show `null`): functions in table constructors (`{ onTick = function() end }`) and a call as a constructor's first item (`{ pcall(function() end) }`). If such a function is a registered callback, the next reload of its file throws `NullPointerException ... "c.prototype.name" is null` in `reroute` while creating the chunk's main closure: nothing of the file runs, and event rewriting stays on (next item).
- **A reloadLuaFile that throws before running leaves event rewriting on**: a compile error or the NPE above leaves `RunLuaInternal` before it resets `rewriteEvents`. From then on every `Events.X.Add/Remove` anywhere silently does nothing and every new closure is rerouted, until some file load completes. Detect with `PZLab.eventsRewriting()`, repair with `PZLab.resetCompiler()`. When the failed file also has nil-named callbacks, even compiling a bridge chunk throws; the bridge then resets the compiler itself. `lab.load_lua` rejects source that doesn't compile and always ends with a completing load.
- **Compiled functions record the last file the engine ran** (statics `FuncState.currentFile/currentfullFile`, set by `RunLua`, stamped in `FuncState.code`): `loadstring` chunks (bridge commands, a mod's runtime `loadstring`) are attributed to that file in prototypes and stack traces (`Lua(Vanilla).pzlab(spawnpoints.lua:1)` after a world loads), and a later reload of that file reroutes their callbacks.
- **`reloadLuaFile` paths (bytecode + verified)**: a path containing `..` anywhere returns nil ("relative paths not allowed" warning). Otherwise `ZomboidFileSystem.validatePrefix` needs the normalized, lower-cased path to start with an allowed prefix: a mod folder (`getAllModFolders`), the cachedir (`Core.getMyDocumentFolder()`, the lab profile) or the game folder; elsewhere `IllegalArgumentException: Invalid prefix found for: ...` is logged and nil returned. Every reload also clears LuaManager's function/table lookup caches, calls `CraftRecipe.onLuaFileReloaded` and marks clothing, hair, underwear, attached-weapon and zombie-zone definitions dirty (~0.2 ms for an empty file). A chunk that raises is logged (`Exception thrown` plus `SEVERE` lines) and the call returns nil.
- **Exceptions inside exposed Java methods don't raise in Lua (verified)**: e.g. `reloadLuaFile` outside the prefixes, `getCell():getGridSquare(nil, 0, 0)` (NPE): logged as an `Exception thrown` block, the call returns nil, `pcall` reports success and the code continues. Calling a method that doesn't exist does raise.
- **Errors caught by pcall are still logged and counted (verified)**: an error raised in a Lua function under `pcall` (`error()` or a runtime error) writes ~100 lines to console.txt (`ExceptionLogger.logException` "Exception thrown" with Java + Lua stack, then `KahluaThread.flushErrorMessage` "dumping Lua stack trace"; runtime errors add a message block and a second dump), adds 2-4 to `getLuaDebuggerErrorCount()` and shows the red ERROR box (`ExceptionLogger.exceptionCount`). A per-frame error is expensive even when caught. Only `pcall` straight on a Java function (`pcall(error, "x")`) is silent. On failure `pcall` returns 4 values: `false`, message, a Lua trace string (`"at error\nat onTick:12\n"`; `error(m, 2)` gives `"2at error..."`), the Java exception. `error("x")` messages have no position prefix; runtime errors end with `" java.lang.RuntimeException"` (`"attempted index: x of non-table: null java.lang.RuntimeException"`); a table passed to `error` comes back as that table. `lab.log.exceptions` lists them.
- **Kahlua standard library (verified)**: missing `next`, `xpcall`, `load`, `loadfile`, `dofile`, `module`, `rawlen`, `gcinfo`, `newproxy`, `io`, `debug` (no `debug.traceback`), `package`, `bit`/`bit32`, `utf8`, `table.unpack/pack/maxn/getn/setn/foreach/foreachi/move` (global `unpack` exists), `math.random/randomseed` (use `ZombRand`), `os.clock/getenv/exit/remove/rename/tmpname/execute`, `coroutine.isyieldable`. Extras: `string.split/trim/contains`, `table.wipe/isempty/newarray`, `loadstream`. `collectgarbage` takes only `"count"`, `"collect"`, `"step"`. `%` truncates like Java (`-7 % 3 == -1`, Lua 5.1 gives 2). The game's `stdlib.lua` implements `string.gmatch` (never ends on a pattern matching `""`) and `table.sort` (recursive quicksort, not stable). `tostring(f)` = `"closure 0x…"`.
- **Non-ASCII characters in Lua string literals are lost (verified)**: files and `loadstring` alike, `'ü'`, `'€'` and even `'\252'` compile to U+FFFD (`'中'` to `'-'`). Kahlua strings are Java strings: `string.char(252)` is `ü` (UTF-16 code units), and strings from Java (`getText`, `getFileReader`, which reads UTF-8) keep theirs. The lab's `to_lua` emits non-ASCII as `string.char(...)`.
- **Timers**: `getTimestampMs()` and `getTimeInMillis()` are `System.currentTimeMillis()` (1 ms steps, verified), `getTimestamp()` whole seconds, `os.time()` seconds with a millisecond fraction; `os.clock` is nil and no global offers a sub-millisecond timer.
- **UIManager add/remove are deferred (verified)**: `AddUI` (`addToUIManager`) and `RemoveElement` (`removeFromUIManager`) only queue (`toAdd`/`toRemove`), applied at the next `UIManager.update`: `UIManager.getUI():size()`/`contains` change on the next frame. `RemoveElement` also cancels a pending add (added and removed in one frame: never shown); `AddUI` of a shown element moves it to the front.
- **Wrapping a vanilla function doesn't intercept event registrations made earlier (verified)**: `Events.OnKeyPressed.Add(ISFPS.onKeyPressed)` stored the original closure, so replacing `ISFPS.onKeyPressed` changes direct calls only. To intercept the event, `Events.X.Remove` the original and `Add` the wrapper.
- **`triggerEvent("OnKeyPressed", 0)` fires every unbound key binding** (unbound = 0): it opened the pause menu and the world map, which pauses the game when shown (`ISWorldMap.shouldPause`, option "map view pause"), ~1 s after `ensurePlayable` had resumed. Use an unused code (e.g. 250) for synthetic key events. `uiState()` doesn't report a visible `ISWorldMap`; close it with `ISWorldMap.HideWorldMap(0)`.
- **World load ends at "Click to Start"** (no Lua ticks there); `wait_state` clicks once the log says `game loading took`.
- **World launch** = vanilla `DebugScenarios:launchScenario`; `IsoWorld` reads global `selectedDebugScenario.startLoc`. Needs `-debug` (DebugScenarios.instance). Sandbox `Zombies = 6` is None.
- **Input**: posted WM_* messages reach GLFW callbacks → engine (`lwjglx Mouse.addMoveEvent`, `glfwGetMouseButton`/`glfwGetKey` caches). No focus needed, real cursor untouched. Mouse Y reads back 1 px lower. If the user's real cursor moves over the lab window, real events interleave. Even resting there it matters: a posted press/release lands at the posted point, but afterwards the engine's mouse position jumps to the real cursor, so the next hover or drag starts there. Keep the real cursor off the window (`window --x X --y Y` moves the lab window).
- **Toggle Inventory is `I`** in B42 (not Tab). Query binds: `getCore():getKey("Toggle Inventory")`.
- **`OnObjectLeftMouseButtonUp` fires ~5 frames after every release, including over UI** — pair with the preceding Down on the same object.
- **`takeScreenshot(name)`** writes `Screenshots/<name>` verbatim (give it `.png`), async on the render thread.
- **`getFileWriter`** only allows txt/json/log/ini/cfg under `<cachedir>/Lua/`; `getFileReader` reads any name there.
- **`Texture:isMaskSet(x, y)`** takes full-tile coordinates (0..getWidthOrig/HeightOrig).
- **Picking** returns the top-most drawn object: things hidden by cutaway walls or behind other objects can't be hovered (lab.hover returns None).
- **`ISCollapsableWindow:close()`** only hides; remove from UIManager yourself.
- **Debug-only vanilla windows** (Loot Stress Test, Fire Brush, Remove Item, Spawn Vehicle) shadow `close` with a button → their title-bar X throws.
- Java-only UI (e.g. `UIDebugConsole`) has no Lua table; `PZLab.ui()` reports its class name.
- B42 translations are JSON (`media/lua/shared/Translate/EN/*.json`).
- **Floor items are never picked**: B42 draws them as 3D models; `UIManager.getLastPicked()` / the object picker return the top object of the square under the cursor (usually the floor), and the world context menu collects floor items from that square (`ISWorldObjectContextMenuLogic.fetch` → `square:getWorldObjects()`; right-click there shows Grab). `isHovered` therefore accepts the item's square for floor items.
- **Corpses and vehicles have their own pickers**: `getLastPicked()` gives the floor under a corpse and nil over a car; the corpse / vehicle pickers resolve any screen point without moving the mouse (aimPoints searches them). A corpse's pick area is offset from its floor position.
- **Picker coordinates differ (verified at zoom 0.75, 1, 1.5)**: every `IsoObjectPicker.Instance:Pick*(x, y)` multiplies by `Core.getZoom(0)` itself, so it takes UI pixels (`getMouseX/Y`), *except* `PickVehicle`, which passes them to `IsoUtils.XToIso` unscaled and needs UI pixels × zoom (`getMouseXScaled()` = `(int)(mouseX * zoom)`, as `ISVehicleMenu` does). E.g. at zoom 0.75 with a car's centre at UI (896, 520): `PickVehicle(896, 520)` misses, `PickVehicle(672, 390)` hits; `PickCorpse` is the reverse. At zoom 1 both agree, which hides the bug. `PickVehicle` only tests the floor player 0 stands on. Use `PZLab.pickVehicle` / `PZLab.pickCorpse` (both UI pixels).
- **Any visible top-level UI under the cursor stops world picking, click-through UI too (bytecode `UIManager.updateTooltip` + verified)**: every frame (unconditionally; nothing else writes it) the engine walks the top-level elements front to back; when the mouse is over a visible one (the top-level element's own `isOverElement`, clipped to `maxDrawHeight`), `getLastPicked()` is nil: no hover, no object tooltip, nothing for code reading `getLastPicked()`. Neither the element's nor its children's mouse-event consumption matters, and a `UIElement`'s hit test is its own rectangle, children not included. Verified over a static click-through `ISPanel` with no child, a consuming child, a click-through child, a consuming `ISInventoryPane` child, and that pane with every descendant click-through and its mouse handlers no-ops: no pick in every case, under the child and beside it. Clicks follow other rules (mouse routing, below): under a click-through element (`setWantMouseEvents(false)`: `ISToolTip`, overlays, previews) a click still reaches the world, and `OnObject*MouseButton*` events get the object under it (`getPicked()`, the context pick, keeps updating while no element consumes the mouse move; verified with a left click). So `PZLab.uiAt` answers clicks, `PZLab.pickBlockedBy` hovering; `isHovered`, `aimPoints` and `lab.hover` use the latter. Details (verified): the object tooltip (`UIManager.getToolTip()`) is skipped, and follows the cursor at +24 px; capture doesn't matter (an `ISPanel` capturing the mouse elsewhere left the pick following the cursor); a collapsed window's body below `maxDrawHeight` doesn't block; an AtomUI element blocks over its child nodes outside its box too. If the world is picked under what looks like a window, the window wasn't a visible top-level element under that point at that moment (e.g. one that moves itself off the cursor): check `PZLab.pickBlockedBy()` and the element's `javaObject` rect in the same chunk. Always-visible vanilla elements block where little or nothing is drawn (and take clicks there): the 1×1 `ISButtonPrompt` at (0, 0), `ISEquippedItem` (x 10-62, y 10-630 at 1280×720), `MoodlesUI` (x 1238-1269 from y 120 down, below the last moodle too).
- **Camera zoom (verified)**: `Core` has no zoom setter (`getCore():setZoom(...)` raises a RuntimeException). Zoom lives in the offscreen buffer (`MultiTextureFBO2`): `Core.doZoomScroll(player, ±1)` (mouse wheel when no UI consumes it, zoom keys) moves the *target* one step along the level list (+1 = out, larger) and the camera eases there in ~0.2 s, also while paused; `getCore():getNextZoom(p, 0)` returns the target. A target not on the list can't be stepped from. Levels come from options `zoomLevels2x` (tile scale 2) / `zoomLevels1x` (`;`-separated percentages, empty = all 25..250, 100 always added); `autozoom` overrides the target every frame. Projection: one tile east = (+64, +32) UI px / zoom.
- **Bridge files and Windows sharing (verified)**: opening a file while another process deletes, replaces (`os.replace`) or truncates it fails with a sharing violation (or not-found), and `getFileReader`/`getFileInput` log every failure as `SEVERE: null java.io.FileNotFoundException ...` plus a stack trace, even inside pcall. With the old single `cmd.txt` (replace + delete) churning for 10 s, 8 such errors; with replace only, 9 in 30 s. Hence the command flags (bridge protocol): 0 in 800 commands plus 27,000 create/delete operations on answered command files. Reading `res.json`/`state.json` while the game rewrites them is safe (0 in 116,000 reads). `listFilesInZomboidLuaDirectory(dir)` lists `<cachedir>/Lua/<dir>` without opening anything.
- **New vehicles drop their key (verified)**: `addVehicleDebug` → `BaseVehicle.addToWorld` → `trySpawnKey` puts the key, by chance, on the floor 10-20 tiles away (`putKeyToWorld`), into a nearby container or the glove box. 30 spawns + `permanentlyRemove` left 2 keys behind; `PZLab.spawnVehicle` removes them (0 left after 30).
- `findBuildings` reads `getWorld():getMetaGrid():getBuildings()` (~10,000 BuildingDefs on the vanilla map, every one with its RoomDefs: name, floor, rectangle), loaded or not. Room rectangles' `x2`/`y2` are exclusive.
- Teleporting 700 tiles takes under a second until the area's squares exist (`areaLoaded`); a building interior without lit rooms or windows has light level 0 even at noon and renders black.
- `SEVERE: Missing tile definition: blends_natural_01_TEST_*` is logged as some vanilla areas load (`log --errors` drops it).
- `teleportTo(x, y, z)` from Lua binds to the int overload and truncates to the tile corner (PZLab.teleport sets the exact position after it).
- `createRandomDeadBody(sq, blood)`: random sex and outfit; worn clothing lives in the corpse container; `container:DoRemoveItem(item)` + `body:checkClothing(item)` strips it. The model appears a few frames after creation (screenshot ~1 s later). Remove with `sq:removeCorpse(body, false)`.
- `addVehicleDebug(script, IsoDirections.X, nil, sq)` fills part containers with random loot; remove with `vehicle:permanentlyRemove()`. A part container is accessible from outside only inside its area (`vehicle:isInArea`) with its door open (trunk door, seat door, front-right door for the glove box): `Vehicles.ContainerAccess` in `media/lua/server/Vehicles/Vehicles.lua`.
- **ISInventoryPane rows**: row i spans `absY + getYScroll() + headerHgt + (i-1)*itemHgt`; `pane.items` is rebuilt every prerender (stack headers are tables whose `items[1]` duplicates `items[2]`); clicking left of `column2` toggles a stack, right of `column3` shows Grab/Drop buttons. Vanilla `ISInventoryPage` windows collapse to their title bar (`maxDrawHeight`) when the mouse leaves unless pinned; their panes stay `isVisible()`.
- The loot window rebuilds its container buttons a moment after containers appear nearby, moving existing ones: look a button up again (and check `inventoryPane.inventory`) after clicking.
- `PZLab.closeUI("ISInventoryPage")` takes vanilla's inventory/loot windows out of the UI for the rest of the session (the Toggle Inventory key only flips their visibility; vanilla never re-adds them). `PZLab.openInventoryWindows()` puts them back.
- Inventory drags: `ISMouseDrag.dragging/draggingFocus` are set on press over a row and cleared when the drop is handled; floor/corpse/vehicle → inventory queues `ISInventoryTransferAction` (dropping onto the floor pane is instant).
- **Sending `esc` with nothing to close opens the pause menu**: vanilla `ToggleEscapeMenu` (on key release) shows `MainScreen` in game, sets game speed 0 and hides every visible UI element (`ISUIHandler.setVisibleAllUI(false)`). The full-screen menu then takes every click and scroll, so world input silently does nothing. Escape doesn't close context menus either: the pause menu hides them and Resume shows them again. Close menus with `PZLab.closeContextMenus()`, recover with `PZLab.ensurePlayable()`, check with `PZLab.uiState()`. Its `escape` field says what the key would do: a focused text entry, or a window whose `isKeyConsumed` takes Escape, gets it first (e.g. the crafting window closes).
- **`setGameSpeed(n)` resets the time multiplier to 1** (`SpeedControls.SetCurrentGameSpeed`); vanilla's keys pair speeds 2/3/4 with multipliers 5/20/40 (`PZLab.setSpeed`). The pause menu's Resume always returns to speed 1. `isGamePaused()` = speed 0 in single player.
- **Mouse routing** (`UIManager.updateMouseButtons` + `UIElement.onMouseDown`, bytecode + verified): front to back, every visible top-level element that captures the mouse or contains the point (height clipped to `maxDrawHeight`, so a collapsed window's body lets clicks through) is offered the press until one consumes it; if none does, the world gets it. Inside an element, its visible children holding the point (strictly right of and below their top-left corner) or capturing are asked first, front to back, recursively; a child that consumes ends it. Otherwise the element's Lua `onMouseDown` decides: true consumes, false doesn't, nil falls back to `consumeMouseEvents` (vanilla `ISPanel:onMouseDown` returns `isWantMouseEvents()`). Every handler on the way runs: a click-through element (`setWantMouseEvents(false)`) still gets `onMouseDown` (and its children theirs), "click-through" only means the press also goes on to the world; a click-through panel whose `onMouseDown` returns true swallows the click; a consuming child of a click-through panel takes presses over it (the parent's own `onMouseDown` doesn't run then). Keys go to a focused text entry first, then to visible top-level elements with `wantKeyEvents` whose Lua `isKeyConsumed(key)` is true, then to `OnKeyStartPressed` (press) / `OnKeyPressed` (release).
- **A dialog destroyed while its text entry has focus keeps the focus** (`ISTextBox:destroy` doesn't unfocus): every key still goes to the invisible entry until a left click or `Core.UnfocusActiveTextEntryBox()` (ensurePlayable does it).
- **Static Java fields read from Lua are a snapshot** taken when the class was exposed: `Core.currentTextEntryBox` is always nil (`getCore():isDoingTextEntry()` is live). Constant references such as `IsoObjectPicker.Instance` work.
- Top-level UI can be `AtomUI` (B42 PZAPI atoms) rather than `UIElement`: it has `getTable`/`getUIName` but no `getControls` or `isConsumeMouseEvents`. Its hit test (`isOverElement`) covers child nodes outside its own box unless `isStencil` (verified: the world isn't picked there); it consumes a press only when an enabled node under the point has a Lua `onMouseButtonDown` returning true (bytecode, `AtomUI.onConsumeMouseButtonDown`), which `uiAt` can't know, so it assumes the press is taken.
- Posted key messages for Alt/Shift/Ctrl (`VK_LMENU` etc.) are seen by `isAltKeyDown()`/`isShiftKeyDown()`/`isCtrlKeyDown()` during a following click.
- **Multiplayer** (verified):
  - A joining client resets its Lua state right after connecting (before character creation) and reloads with the server's mods: everything a test set in Lua before joining is gone; a mod the server doesn't list isn't loaded in the client.
  - `getOnlineUsername()` throws (NPE, logged even under pcall) until the player exists; `isClient()` is true from the connection on, also at the menu.
  - Loading a server's mods, the animator walks every mod's `media/AnimSets` and `media/actiongroups` and logs a `NoSuchFileException` block for each one a mod lacks (client and server); `log --errors` drops them as noise, `log.exceptions` lists them.
  - The dedicated server's `UPnP` option defaults to true: a first start without the lab's ini maps the port on the router (24 h lease, `PortMapper.addMapping`); the lab sets `UPnP=false` before every start.
  - **Teleports**: a client-side `teleportTo` far away moves the player (the server follows) but the client draws only a patch around the player, the rest black; the admin command `/teleportto x,y,z` (what `goto` uses) streams the area properly. It takes fractions (`Float.parseFloat`); the server only sends the client a `Teleport` packet (it never moves the player itself), the client sets x/y/z and reports its position, which the server's copy then follows (`PlayerPacket` also sets the connection's `releventPos`). Whole-number coordinates put the player on the tile's north-west corner, where collision pushes them onto a neighbouring tile (6119,5474 → x 6118.97): aim at the centre. A position the client sets itself is checked by the server's no-clip anti-cheat (`AntiCheatNoClip`, skipped for 500 ms after a server teleport and for admins with no-clip on) against `releventPos`, and a move across a blocked edge is answered with a teleport back there. No snap-backs were seen with server teleports to tile centres, also beside a vehicle. A target on a vehicle's tile: its collision pushes the player to the tile's edge or the next tile.
  - **Off the map** (e.g. near 0,0) the player has no square on either side and queued timed actions never start (the player stays busy, nothing moves, inventory-only transfers included); `goto` refuses such targets (`PZLab.onMap`).
  - **`addVehicleDebug` on a spot vanilla rejects** (`IsoChunk.doSpawnedVehiclesInInvalidPosition`: indoors without a vehicle zone, or overlapping another vehicle) still returns a vehicle, never added to the world: its transform stays at 0,0, so `getAreaCenter`/`getWorldPos` give points near the map origin; on a server its id stays -1 and no client ever sees it. A goto computed from such an area centre lands off the map. `spawnVehicle` raises instead (`PZLab.vehiclePlaced`).
  - The chat window (`ISChat`, ~500×200 at the left) covers the world: no picking/hover there (`lab.join` closes it unless `close_chat=False`). The server's random weather often starts in dense fog (white screenshots); the `stopweather` console command doesn't clear fog, admin climate overrides do (`PZLab.clearWeather`, fades in a few seconds).
  - A container object sent with `transmitCompleteItemToClients` while it already holds items made the client log `Error: Dupe item ID` for them: send it empty, then the items (`spawnContainer` does).
  - On a server, removals far from every player are lost: `getGridSquare` still returns squares of cells no player uses, the removal "works", but when a player comes back the objects are there again (the cell's save didn't take it); `DoRemoveItem` of food there also fails to send `ItemStats` (`Unable to resolve container location`). After a cell reloads, the world holds new Java objects (items keep their ids). `clearFixtures` handles both (reach rule, refresh by id / modData marker).
  - The client's own cache of a server lives in `<profile>/Saves/Multiplayer/<ip>_<port>_<md5(user)>/` (pruned with the other saves); whether a character exists is asked from the server (`checkSavePlayerExists` → `ClientPlayerDB`).

## Not yet supported / caveats

- Windows only for input injection, OS capture and process control (Linux/macOS: launch/bridge code is mostly portable but untested).
- Keys are sent by US-layout virtual-key codes; on other keyboard layouts letter keys may map to different game keys.
- Display scaling other than 100% is untested (GLFW is DPI-aware, so client pixels should still equal UI pixels).
- Multiplayer: no co-op hosting (in-game host) or Steam servers; joins other servers only by ip/port/account; clients run with `-debug`, so they need admin (`ConnectWithDebug`) accounts. Several lab sessions at once need distinct profiles and distinct `server.port`s (several clients on one lab server work).
- Controller input.
- VS Code panel viewer (stream frames + forward input) is a planned layer on top of `capture`/`inputs`.
