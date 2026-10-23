"""Launching and stopping the game client and the lab server processes."""

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


def server_command(cfg):
    """The install's dedicated-server launch spec (detect.server_launch_spec) with Steam off, the
    lab server profile as -cachedir, loopback binding and the lab's admin account."""
    server = cfg["server"]
    spec = detect.server_launch_spec(cfg.game_dir)
    vm_args = spec["vm_args"]
    if server["heap"]:
        vm_args = [a for a in vm_args if not a.startswith("-Xmx")] + ["-Xmx" + server["heap"]]
    if server["debug"]:
        vm_args = vm_args + ["-Ddebug"]
    return [str(java_exe(cfg.game_dir)), *vm_args, "-cp", os.pathsep.join(spec["classpath"]),
            spec["main_class"], "-cachedir=" + str(cfg.server_profile_dir), "-servername", server["name"],
            "-adminusername", server["admin_user"], "-adminpassword", server["admin_password"],
            "-nosteam", "-ip", server["ip"], "-port", str(server["port"]), "-udpport", str(server["udp_port"]),
            *server["args"]]


def read_pid_file(pid_file):
    try:
        return json.loads(pid_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def running_pid_file(pid_file):
    info = read_pid_file(pid_file)
    if info and win32.process_alive(info["pid"]):
        return info["pid"]
    return None


def read_pid(cfg):
    return read_pid_file(cfg.pid_file)


def running_pid(cfg):
    return running_pid_file(cfg.pid_file)


def start_process(args, cwd, pid_file, log_path, stdin_path=None, what="lab game"):
    """Starts a detached process (no console window, outlives the caller) with stdout+stderr in
    log_path and stdin read from stdin_path (None: no input); records it in pid_file."""
    if running_pid_file(pid_file):
        raise RuntimeError("%s already running (pid %s)" % (what, running_pid_file(pid_file)))
    log = open(log_path, "wb")
    stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    base = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        # Break away from any job object so the game outlives the shell that started it.
        for flags in (base | CREATE_BREAKAWAY_FROM_JOB, base):
            try:
                proc = subprocess.Popen(args, cwd=str(cwd), stdin=stdin, stdout=log,
                                        stderr=subprocess.STDOUT, creationflags=flags, close_fds=True)
                break
            except OSError:
                if flags == base:
                    raise
    finally:
        log.close()
        if stdin_path:
            stdin.close()
    pid_file.write_text(json.dumps({"pid": proc.pid, "started": time.time(), "args": args}))
    return proc.pid


def launch(cfg):
    return start_process(command(cfg), cfg.game_dir, cfg.pid_file, cfg.profile_dir / "stdout.log")


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
