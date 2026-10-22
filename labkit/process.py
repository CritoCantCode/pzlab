"""Launching and stopping the game process for the lab profile."""

import json
import os
import subprocess
import time

from . import detect, win32

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def java_exe(game_dir):
    for rel in ("jre64/bin/java.exe", "jre64/bin/java", "jre/bin/java.exe", "jre/bin/java"):
        if (game_dir / rel).exists():
            return game_dir / rel
    raise FileNotFoundError("no bundled Java runtime under %s" % game_dir)


def command(cfg):
    """The game's own launch spec (ProjectZomboid64.json) with Steam off and the lab profile."""
    spec = detect.launch_spec(cfg.game_dir)
    vm_args = spec["vm_args"]
    if cfg["heap"]:
        vm_args = [a for a in vm_args if not a.startswith("-Xmx")] + ["-Xmx" + cfg["heap"]]
    return [str(java_exe(cfg.game_dir)), *vm_args, "-cp", os.pathsep.join(spec["classpath"]),
            spec["main_class"], "-cachedir=" + str(cfg.profile_dir), *cfg["game_args"]]


def read_pid(cfg):
    try:
        return json.loads(cfg.pid_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def running_pid(cfg):
    info = read_pid(cfg)
    if info and win32.process_alive(info["pid"]):
        return info["pid"]
    return None


def launch(cfg):
    if running_pid(cfg):
        raise RuntimeError("lab game already running (pid %s)" % running_pid(cfg))
    args = command(cfg)
    log = open(cfg.profile_dir / "stdout.log", "wb")
    base = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    # Break away from any job object so the game outlives the shell that started it.
    for flags in (base | CREATE_BREAKAWAY_FROM_JOB, base):
        try:
            proc = subprocess.Popen(args, cwd=str(cfg.game_dir), stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, creationflags=flags, close_fds=True)
            break
        except OSError:
            if flags == base:
                raise
    cfg.pid_file.write_text(json.dumps({"pid": proc.pid, "started": time.time(), "args": args}))
    return proc.pid


def game_window(pid):
    """The GLFW game window of the process, or None."""
    for w in win32.windows_of(pid):
        if w["cls"].startswith("GLFW") and w["visible"]:
            return w["hwnd"]
    return None


def kill(cfg):
    pid = running_pid(cfg)
    if pid:
        win32.terminate(pid)
    return pid
