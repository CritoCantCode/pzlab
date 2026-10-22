# pzlab — agent reference

Drives a real Project Zomboid (Build 42) instance for automated testing of any mod: launch into a
world unattended, run Lua inside the live game, inject mouse/keyboard, take screenshots, read
logs. Built for Claude; terse by design. Self-contained: everything is in this repository, nothing
names a machine, user or mod; the folder can be copied anywhere.

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
| `start [--mod-dir PATH]... [--mod NAME]... [--at X Y Z] [--menu]` | setup + launch + autostart world + dismiss "Click to Start"; ~40 s cold. `--mod-dir` links any mod folder or Workshop item (`Contents/mods/<one mod>`); `--mod` names one from config. `--menu` stops at main menu. |
| `world [--at X Y Z]` | from main menu of a running game, launch a fresh world (~25 s) |
| `stop [--force]` | graceful `quitToDesktop`, kill after 20 s |
| `status` | pid, window, harness heartbeat (`state` menu/ingame, `age_s`, ticks, player) |
| `lua CODE` / `lua -f FILE [--timeout MS]` | run Lua in game; single expressions auto-`return`ed |
| `shot [--crop X Y W H] [--scale F] [--out P]` | in-game screenshot (frame buffer, UI included) → path; Read it to view |
| `shot --os` | PrintWindow capture — works with no/halted harness, menus, loading screens |
| `move X Y`, `click X Y [--button right] [--double]`, `drag X1 Y1 X2 Y2`, `wheel X Y N` | mouse |
| `key NAME`, `keydown/keyup NAME`, `chord ctrl a`, `type TEXT` | keyboard (names in labkit/inputs.py KEYS) |
| `log [--errors] [--all] [--tail N] [--grep RE]`, `mark` | console.txt since last mark; `--errors` drops known vanilla noise |
| `reload FILE` | hot-reload a mod Lua file by its source path (resolved through the profile's mod links) |
| `run SCRIPT.py` | Python test script with `lab` (labkit.Lab) predefined; see scripts/probe_aim.py |

Python API (`from labkit import Lab`): `lab.start/stop/status/launch_world/wait_state`,
`lab.lua(code)` (raises LuaError), `lab.bridge.exec(code)` (raw result), `lab.input.move/click/
drag/wheel/tap/chord/type_text`, `lab.hover(lua_obj_expr)` (mouse onto a world object until the
engine picks it), `lab.screenshot/os_screenshot`, `lab.log.mark/lines/errors`, `lab.reload(file)`.

Coordinates everywhere are game UI pixels = window client pixels (1280×720 by default).

## Layout

- Config layers (later wins): built-in defaults (`labkit/config.py`) → `lab.json` (portable, committed: resolution, world, game args) → `lab.local.json` (this machine, gitignored: game_dir, options_overrides) → env `PZLAB_GAME_DIR`, `PZLAB_PROFILE` (profile dir, relative to the repo root) → CLI. Keys: `mods` (`name: path`), `enabled`, `world` (map, x, y, z, sandbox, god, closeUI, onStart), `heap` (null = game's own), `keep_saves`, `window`.
- `profile/` (gitignored; `profile*/` for alternates) — the game's `-cachedir`: options.ini, mods/ (links + default.txt), Lua/pzlab/ (bridge files), Saves/, Screenshots/, shots/, console.txt.
- `harness/PZLabHarness/` — in-game mod (always active in the lab): PZLab_Core (log, files, JSON), PZLab_Bridge (commands, heartbeat, steps), PZLab_Boot (autostart world), PZLab_Tools (helpers).
- `labkit/` — Python: config, detect (game dir, launch spec, version facts read from the install), profile, process, bridge, inputs, capture, imaging/png (Pillow-optional), logs, lab; `luacheck/` (precompiled `LuaCheck.class`, Java 17 bytecode, + source).

Isolation: `-cachedir` keeps everything in the profile; `-nosteam -nosound -novoip -debug`. Outside this repository the lab only *reads*: the game install, Steam's library list, and (in `init` only) two keys of `~/Zomboid/options.ini`. Saves are pruned to `keep_saves`.

Version-sensitive facts are read from the installed game, not hardcoded: JVM args/classpath/main class from `ProjectZomboid64.json` (only the highest OS-version entry applies), the mods reset-marker name from `GameWindow.class` (`resetDefaultModsForNewRelease("NN_NN")`). If a future build moves them, `doctor` and the stall report point at it.

## Bridge protocol (profile/Lua/pzlab/)

- `cmd.txt`: `PZLAB <seq> <timeoutMs>\n<lua chunk>` (atomic replace; controller deletes it after the reply).
- Harness polls every 50 ms from `OnFETick` (menu) / `OnTickEvenPaused` (in game), runs the chunk synchronously via `pcall`, writes `res.json` `{seq, ok, value|error, out, frames, ms}`.
- Multi-tick: return `PZLab.steps{fn(ctx), n_frames, PZLab.waitMs(ms), PZLab.waitUntil(fn, timeoutMs), ...}`; result = last function's return. **No coroutines** (see traps).
- `state.json` heartbeat every 0.5 s. Stale heartbeat + live process = loading, "Click to Start", or Lua halted → `shot --os`.
- `PZLab.out(...)` appends lines to the reply's `out`.

## Lua helpers (PZLab.*)

`player()`, `square(x,y,z)`, `teleport(x,y,z)`, `toScreen(x,y,z)`, `objectScreenPos(obj)`, `aimPoints(obj, n)`, `describeObject(obj)`, `describeContainer(c)`, `objectsAt(x,y,z)`, `findContainers(radius)`, `ui(includeHidden)`, `closeUI(type)`, `mouse()` (pos + engine-picked object), `give(fullType, n)`, `addToContainer(c, fullType, n)`, `screenshot(name)`, `reload(path)`, `launchWorld(cfg)`, `json(v)`, `log(msg)`, `readFile/writeFile(name)`.

## Engine facts and traps (verified on 42.21)

- **Mods load at boot from `mods/default.txt`** — but if `mods/reset-mods-<NN_NN>.txt` (42_00 in 42.21) is missing the game creates it and empties default.txt. Setup writes the marker (name read from the jar).
- **First launch in a fresh profile shows the Terms of Service** (`termsOfServiceVersion` in options.ini). Human acceptance only — see "New machine".
- **`-debug` + a Lua error opens the Lua debugger and halts all Lua** (harness included). Harness calls `UIManager.setShowLuaDebuggerOnError(false)` at load. If halted anyway: `shot --os`, Resume button ≈ (560, 21), or `doLuaDebuggerAction("Resume")`.
- **Coroutines crash Kahlua**: a coroutine finishing inside `pcall` from an event handler → NPE in `KahluaThread.luaMainloop` and the event stops firing. Vanilla never uses coroutines.
- **World load ends at "Click to Start"** (no Lua ticks there); `wait_state` clicks once the log says `game loading took`.
- **World launch** = vanilla `DebugScenarios:launchScenario`; `IsoWorld` reads global `selectedDebugScenario.startLoc`. Needs `-debug` (DebugScenarios.instance). Sandbox `Zombies = 6` is None.
- **Input**: posted WM_* messages reach GLFW callbacks → engine (`lwjglx Mouse.addMoveEvent`, `glfwGetMouseButton`/`glfwGetKey` caches). No focus needed, real cursor untouched. Mouse Y reads back 1 px lower. If the user's real cursor moves over the lab window, real events interleave.
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

## Not yet supported / caveats

- Windows only for input injection, OS capture and process control (Linux/macOS: launch/bridge code is mostly portable but untested).
- Keys are sent by US-layout virtual-key codes; on other keyboard layouts letter keys may map to different game keys.
- Display scaling other than 100% is untested (GLFW is DPI-aware, so client pixels should still equal UI pixels).
- Multiplayer (co-op host + client), controller input, multiple simultaneous lab instances.
- VS Code panel viewer (stream frames + forward input) is a planned layer on top of `capture`/`inputs`.
