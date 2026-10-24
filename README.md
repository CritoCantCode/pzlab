# pzlab

A test lab that drives a real Project Zomboid (Build 42) game so you, or an AI coding agent, can
test any mod in the actual game, unattended.

One command launches the game into a world with your mod loaded. From there you can run Lua inside
the live game, click and drag with real mouse input, take screenshots, read the logs as structured
data and tear down whatever the test created. It does single player and multiplayer (a local
dedicated server with one or more clients joined to it), and every command answers in JSON.

pzlab was built for AI agents and works just as well by hand. [AGENT.md](AGENT.md) is the full
reference for agents including every command, the Python and Lua APIs, the bridge protocol and a long list of verified
engine traps.

## Why use it

- **It tests the real game.** Your mod runs against vanilla's own Lua, the Java engine and the real
  UI, not a mock. Event order, UI routing, multiplayer item transactions and hot reload all behave
  the way players will see them.
- **It runs unattended.** `start` goes from nothing to a character standing in the world in about
  40 seconds. It creates the world, dismisses "Click to Start" and switches off the Lua debugger's
  break-on-error so a stray error doesn't freeze the run.
- **It leaves your own game alone.** The lab runs in its own profile inside this repository
  (`-cachedir`), with `-nosteam`. Your saves, mod list and settings in `~/Zomboid` are never
  written; `init` only reads two keys from your `options.ini`. The lab server binds to loopback
  with UPnP off, so nothing is exposed to the network.
- **Input understands the UI.** It knows what a click at a point would hit and which window stops
  the world from being picked under the cursor. `drag_row` checks each phase of a drag (row under
  the mouse, drag started, target hovered, drop handled) and names the window in the way when one
  is. Input is posted to the game window, so it needs no focus and doesn't move your cursor.
- **Fixtures clean up after themselves.** Spawn items, bags with contents, containers, furniture,
  B42 workstations (forges, kilns), corpses and vehicles. Furniture, workstations, corpses and
  vehicles are made through vanilla's own map-loading, build and spawn code, so they match what a
  player meets. `clear_fixtures()` removes every fixture wherever it has moved to: floor, a
  container, the player's hands.
- **Multiplayer tests check the server's truth.** Spawn on the server, act on the client through
  vanilla timed actions, then ask the server where the item really is. A client's view and the
  server's often disagree, and that is where multiplayer bugs hide.
- **Logs are data.** `log --exceptions` returns one JSON entry per engine-logged exception, with the
  file, line and Lua stack. A test can assert that a step logged nothing; `log --errors` filters out
  known vanilla noise.
- **Lua checks need no game or JDK.** `check` compiles your files with the game's own Kahlua
  compiler, using the Java bundled with the game.
- **It keeps up with game updates.** Launch arguments, the classpath, the mod-reset marker and the
  sandbox version are read from the installed game, not hardcoded. When a build moves them,
  `doctor` points at what changed.
- **It knows the engine's traps.** AGENT.md records dozens of verified engine behaviours that cost
  time to rediscover: coroutines crash Kahlua, errors caught by `pcall` are still logged at about
  100 lines each, hot reload reroutes event callbacks by function name, any visible window stops
  world picking, and more.
- **It pushes mods toward good practice.** AGENT.md asks agents to suggest faster, safer and more
  compatible approaches (wrap vanilla instead of replacing it, no allocation in per-frame code, a
  public API for what other mods may extend) and to back them with lab measurements.
- **Setup is light.** Python's standard library and the installed game are all it needs. Pillow
  speeds up screenshots if you have it.

## Requirements

- Windows 10 or 11
- Python 3.8 or later (stdlib only; Pillow optional)
- Project Zomboid Build 42, installed through Steam (libraries are detected automatically) or
  anywhere else (`init --game-dir`)

You don't need a JDK, a dedicated server download or Steam running.

## Quick start

Clone the repository anywhere; everything the lab needs or writes stays inside it.

```sh
git clone https://github.com/CritoCantCode/pzlab.git
cd pzlab
python pzlab.py doctor                     # what it found: game folder, launch command, ToS status
python pzlab.py init                       # writes lab.local.json for this machine (gitignored)
python pzlab.py start --mod-dir "<path to your mod>"
```

`--mod-dir` takes a mod folder (one with `common/` or a version folder such as `42/`) or a Workshop
item (`Contents/mods/<one mod>`). Repeat it to load several mods. Without it the lab starts with
vanilla only.

**Terms of Service.** pzlab never accepts the game's Terms of Service for you. `init` carries over
an acceptance you already made in your own game. If there is none, run
`python pzlab.py start --menu` once, click ACCEPT in the game window yourself, and the lab profile
keeps it.

Once the game is in the world:

```sh
python pzlab.py lua "PZLab.player():getInventory():getItems():size()"   # → {"ok": true, "value": 7, ...}
python pzlab.py goto grocery               # teleport to a catalogued test location, wait until loaded
python pzlab.py shot --check               # screenshot path, plus anything (pause menu, dialog) in the way
python pzlab.py log --exceptions           # [] when nothing went wrong
python pzlab.py stop
```

Keep your own mouse cursor off the lab window while tests run. Real mouse events mix with the
posted ones, and while your cursor rests over the window the engine jumps back to it after each
posted click.
`python pzlab.py window --x X --y Y` moves the window out of the way.

## Writing a test

A test is a Python script with `lab` already defined. This one picks an apple up off the floor
through the real inventory UI and checks where it ended up:

```python
"""Pick an apple up off the floor through the real inventory UI and check where it ended up."""

off = lab.log.size()                       # remember where the log was
lab.ensure_playable()                      # no pause menu, no stray context menu, speed 1
lab.goto("parking-lot")                    # a catalogued spot: open asphalt

apple_id = lab.lua("""
    local p = PZLab.player()
    local floor = PZLab.spawnWorldItem("Base.Apple", math.floor(p:getX()) + 1, math.floor(p:getY()), 0)
    return floor:getItem():getID()
""")

with lab.ui_layout():                      # windows go back where they were afterwards
    panes = lab.show_inventory_panes()
    loot, inv = panes["loot"]["pane"], panes["inventory"]["pane"]
    row = next(r for r in lab.pane_rows(loot) if r["fullType"] == "Base.Apple")
    lab.drag_row(loot, row["index"], inv)  # a real mouse drag, checked phase by phase
    lab.wait_until("not PZLab.playerBusy()")

where = lab.lua("return PZLab.placeText(PZLab.locate(%d))" % apple_id)
assert where.startswith("character:"), where
assert not lab.log.exceptions(since=off)   # and nothing went wrong along the way
lab.clear_fixtures()
print("PASS", where)
```

```sh
python pzlab.py run path/to/test_pickup.py
```

Keep your mod's tests in the mod's own repository and point `run` at them; pzlab itself stays
mod-agnostic. To drive the lab from your own Python program instead, use
`from labkit import Lab; lab = Lab()`. The probes in [scripts/](scripts/) are larger working
examples: fixtures, hovering, UI state, camera zoom, layouts, multiplayer.

## Using it with an AI agent

Point the agent at [AGENT.md](AGENT.md). For example:

> Read `pzlab/AGENT.md`, start the lab with my mod at `<path>`, and check that the new context-menu
> option moves the item into the player's main inventory, in single player and multiplayer.

AGENT.md is written for agents: terse, complete, with a "verified" note on every claim about the
engine. All output is JSON, so agents read results directly. When an agent works inside this
repository, [CLAUDE.md](CLAUDE.md) sends Claude Code to AGENT.md first.

AGENT.md also asks agents to turn safety, leak, error-recovery, per-frame cost, multiplayer and
compatibility checks into permanent tests in the mod's suite, rather than one-off looks.

## Multiplayer

```sh
python pzlab.py start --join --mod-dir "<path to your mod>"   # lab server + one client in the world (~95 s cold)
python pzlab.py server lua "PZLab.serverInfo()"               # Lua in the server's (authoritative) state
python pzlab.py server cmd players                            # any server console command
python pzlab.py stop --all                                    # client and server
```

The lab server comes from the installed game, runs in its own profile (`profile-server/`) and
starts a fresh world each time unless you pass `--keep-world`. More clients join with their own
profiles (`lab.client("profile-2")`). The Multiplayer section of AGENT.md covers accounts,
server-side fixtures and how item transfers really travel between client and server.

## Commands at a glance

All commands are `python pzlab.py <command>`. The full list with every option is in AGENT.md.

| Area | Commands |
|---|---|
| Setup | `doctor`, `init`, `check PATH...` |
| Game | `start`, `world`, `stop`, `status`, `window` |
| Lua | `lua CODE`, `lua -f FILE`, `wait EXPR`, `reload FILE`, `load-lua` |
| Input | `move`, `click`, `drag`, `wheel`, `hover`, `key`, `chord`, `type`, `drag-row` |
| Seeing | `shot`, `shot --os`, `ui`, `panes`, `zoom` |
| Places | `goto NAME`, `goto X Y Z`, `locations` |
| UI state | `playable`, `layout save`, `layout restore` |
| Logs | `log`, `log --errors`, `log --exceptions`, `mark` |
| Multiplayer | `start --join`, `join`, `server start/stop/status/lua/cmd/user/log/wait` |
| Tests | `run SCRIPT.py` |

## How it works

- **Harness mod.** `harness/PZLabHarness/` is a small Lua mod that is always enabled in the lab, on
  the lab server too. It provides the `PZLab.*` helpers and runs the command bridge.
- **Bridge.** The controller writes a Lua chunk to a file under `profile/Lua/pzlab/`, then an empty
  flag file. The harness polls the folder every 50 ms, runs the chunk inside the game and writes
  the result to `res.json`. A heartbeat in `state.json` tells the controller whether the game is at
  the menu, in the world, loading or stuck. Multi-frame jobs (wait, then act, then check) run as
  `PZLab.steps`.
- **Input.** Mouse and keyboard messages are posted straight to the game window and reach the
  engine like real input.
- **Screenshots.** `shot` reads the game's frame buffer, UI included. `shot --os` captures the
  window from outside, which still works on loading screens or when Lua has halted.
- **Config.** Settings are layered: built-in defaults, then `lab.json` (committed: resolution, start
  location, sandbox, game arguments), then `lab.local.json` (this machine), then environment
  variables, then command-line options.

## Repository layout

| Path | What |
|---|---|
| `pzlab.py` | command-line entry point |
| `labkit/` | the Python library: launch, bridge, input, capture, logs, lab server, test locations |
| `harness/PZLabHarness/` | the in-game harness mod (client, server and shared Lua) |
| `scripts/` | probe scripts that exercise and re-verify the lab's features |
| `lab.json` | portable lab settings |
| `AGENT.md` | the full reference |
| `lab.local.json`, `profile*/` | this machine's settings and the game profiles; created at runtime, gitignored |

## Limitations

- Windows only for input, window capture and process control. The launch and bridge code is
  mostly portable but untested elsewhere.
- Keys are sent as US-layout key codes; on other layouts letter keys may land on different game
  keys.
- Display scaling other than 100% is untested.
- Multiplayer covers the local lab server and direct ip/port joins. In-game co-op hosting and Steam
  servers aren't supported, and clients run with `-debug`, so they need admin accounts.
- No controller input.

## Contributing

Changes should serve any mod, not one: give a new capability a generic name, document it in
AGENT.md and verify it against the live game (`doctor`, `start`, the affected command, `stop`).
Keep Python stdlib-only. Keep machine paths, user names and mod-specific code out of committed
files. Run `python pzlab.py check harness` after every Lua change. [CLAUDE.md](CLAUDE.md) has the
complete rules.

## License

[MIT](LICENSE). Use, change and share pzlab freely, commercially or not, as long as you keep the
copyright and license notice with copies of it.
