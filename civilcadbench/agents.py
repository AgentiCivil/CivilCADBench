"""Start a coding agent once in every workspace of a run folder and stop it at the time limit.

container (default)   each task in its own container of the image built from examples/docker: no network apart from
                      the allowlist proxy, the workspace as the only mount with task content, 8 CPUs, 16 GB of memory,
                      1024 processes, non-root. These are the limits of docs/PROTOCOL.md.
local (--local)       the agent runs on this machine, in the workspace folder. Nothing keeps it from reading other
                      files of the machine, the ground truth among them, so a local run is for trying things out.

Agents: Claude Code, Codex CLI, or any command (--agent-cmd). A command is run by bash inside the workspace; the
prompt, the task id and the model reach it as $CCB_PROMPT, $CCB_TASK, $CCB_LEVEL, $CCB_MODEL and $CCB_WORKSPACE, and
as {prompt}, {task}, {model} and {workspace} in the command text.

A task is finished when _logs/<task>/.exit_code exists. A task that was started and has no exit code was interrupted:
its workspace and logs are moved to _interrupted/ and it starts again from a fresh workspace.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

PROMPTS = {
    "L1": "请阅读当前目录的 TASK.md 并完成任务，产出 output.dxf 和 NOTES.md。全程不需要向我确认，直接做完。",
    "L2": "请阅读当前目录的 TASK.md 并完成任务，在 output/ 目录下产出每页的 DXF，并写出 NOTES.md。全程不需要向我确认，直接做完。",
}
PROMPTS["L3"] = PROMPTS["L1"]
LIMITS = {"L1": 7200, "L2": 21600, "L3": 7200}          # seconds: 2 hours for a page, 6 hours for a set
SANDBOX = ["--cpus", "8", "--memory", "16g", "--pids-limit", "1024", "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
TIMED_OUT = 124
STOPPING = threading.Event()        # ccb itself is being stopped
RUNNING = {}                        # task -> function that stops its agent


class Interrupted(Exception):
    pass


def agent_command(cfg, level, name, workspace, log):
    """argv of the agent, and the environment variables a custom command gets."""
    prompt, model = PROMPTS[level], cfg["model"] or ""
    inside = "/work" if cfg["docker"] else str(workspace)
    env = {"CCB_PROMPT": prompt, "CCB_TASK": name, "CCB_LEVEL": level, "CCB_MODEL": model, "CCB_WORKSPACE": inside}
    if cfg["agent_cmd"]:
        cmd = cfg["agent_cmd"]
        for key, value in (("{prompt}", prompt), ("{task}", name), ("{model}", model), ("{workspace}", inside)):
            cmd = cmd.replace(key, shlex.quote(value))
        return ["bash", "-lc" if cfg["docker"] else "-c", cmd], env
    if cfg["agent"] == "claude-code":
        return ["claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
                "--dangerously-skip-permissions", "--disallowedTools", "WebFetch", "WebSearch", "Task", "Agent"], {}
    if cfg["docker"]:       # the container is the sandbox
        return ["codex", "exec", "-m", model, "-c", 'model_reasoning_effort="high"', "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check", "-C", "/work", "--json", "-o", "/work/last_message.md", prompt], {}
    return ["codex", "exec", "-m", model, "-c", 'model_reasoning_effort="high"', "-c", 'approval_policy="never"',
            "-s", "workspace-write", "--skip-git-repo-check", "-C", str(workspace), "--json", "-o", str(log / "last_message.md"), prompt], {}


def docker_ready(cfg):
    if not shutil.which("docker"):
        sys.exit("docker not found. Install Docker and build the two images of examples/docker, or pass --local "
                 "to run the agent on this machine without isolation.")
    for image in (cfg["image"], cfg["proxy_image"]):
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
            sys.exit(f"image {image} not found. Build it first:\n"
                     "  cd examples/docker && cp allowlist.example allowlist    # then put the host of your model endpoint in it\n"
                     "  docker build -t cadbench/proxy -f proxy.Dockerfile . && docker build -t cadbench/agent -f agent.Dockerfile .")
    sock = Path(cfg["sock"])
    sock.mkdir(parents=True, exist_ok=True)
    sock.chmod(0o777)
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
    if cfg["proxy"] not in names:
        subprocess.run(["docker", "rm", "-f", cfg["proxy"]], capture_output=True)
        r = subprocess.run(["docker", "run", "-d", "--name", cfg["proxy"], "--restart", "unless-stopped", "--network", "bridge",
                            "-v", f"{sock}:/proxy", cfg["proxy_image"]], capture_output=True, text=True)
        if r.returncode:
            sys.exit("the proxy container did not start: " + r.stderr.strip()[:300])
        time.sleep(2)


def run_task(cfg, run, name, level):
    """Run the agent on one task. Returns (exit code, seconds); 124 means stopped at the time limit."""
    ws, log = run / name, run / "_logs" / name
    (log / "agent_home").mkdir(parents=True, exist_ok=True)
    argv, env = agent_command(cfg, level, name, ws, log)
    limit = cfg["limits"][level]
    out_name = "stdout.log" if cfg["agent_cmd"] else "events.jsonl"
    container = "cadbench-" + hashlib.sha1(f"{run}/{name}".encode()).hexdigest()[:12]
    if cfg["docker"]:
        for folder in (ws, log):
            subprocess.run(["chmod", "-R", "a+rwX", str(folder)], capture_output=True)       # the container writes as uid 1000
        head = ["docker", "run", "--rm", "--name", container, "--network", "none", "-v", f"{ws}:/work", "-v", f"{cfg['sock']}:/proxy", *SANDBOX]
        if cfg["env_file"]:
            head += ["--env-file", cfg["env_file"]]
        for k, v in env.items():
            head += ["-e", f"{k}={v}"]
        if cfg["agent_cmd"]:
            pass
        elif cfg["agent"] == "claude-code":
            m = cfg["model"]
            for k in ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                      "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
                head += ["-e", f"{k}={m}"]
            head += ["-e", "CLAUDE_CONFIG_DIR=/home/agent/.claude", "-e", "HOME=/home/agent", "-v", f"{log / 'agent_home'}:/home/agent/.claude"]
        else:
            for f in ("config.toml", "auth.json"):      # a copy of the key for the time of the run; deleted below
                shutil.copyfile(Path(cfg["codex_home"]) / f, log / "agent_home" / f)
            subprocess.run(["chmod", "-R", "a+rwX", str(log / "agent_home")], capture_output=True)
            head += ["-e", "CODEX_HOME=/home/agent/.codex", "-v", f"{log / 'agent_home'}:/home/agent/.codex"]
        argv, cwd, penv = head + [cfg["image"]] + argv, None, None
    else:
        cwd, penv = str(ws), {**os.environ, **env}

    started = time.time()
    (log / ".t_start").write_text(f"{int(started)}\n")
    with open(log / out_name, "wb") as out, open(log / "stderr.log", "wb") as err:
        p = subprocess.Popen(argv, cwd=cwd, env=penv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)

        def stop():
            if cfg["docker"]:
                subprocess.run(["docker", "kill", container], capture_output=True)
            else:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                p.wait(30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait()

        RUNNING[name] = stop
        try:
            rc = p.wait(timeout=limit)
        except subprocess.TimeoutExpired:       # the time limit: the run is stopped and what is in the workspace is scored
            stop()
            rc = TIMED_OUT
        finally:
            RUNNING.pop(name, None)
    if STOPPING.is_set():                       # ccb itself was stopped: no exit code is written, the task starts again next time
        (log / "agent_home" / "auth.json").unlink(missing_ok=True)
        raise Interrupted(name)
    if cfg["docker"]:
        subprocess.run(["docker", "kill", container], capture_output=True)
        (log / "agent_home" / "auth.json").unlink(missing_ok=True)
        # hand the files back to the account that runs ccb
        subprocess.run(["docker", "run", "--rm", "--user", "0", "--network", "none", "--entrypoint", "chown", "-v", f"{ws}:/w",
                        "-v", f"{log}:/l", cfg["image"], "-R", f"{os.getuid()}:{os.getgid()}", "/w", "/l"], capture_output=True)
    seconds = int(time.time() - started)
    (log / ".t_end").write_text(f"{int(time.time())}\n")
    (log / ".exit_code").write_text(f"{rc}\n")
    return rc, seconds


def run_all(cfg, run, todo, export_one, say=print):
    """todo: (task name, level, task folder in the data). Returns {task: {"exit_code", "seconds"}} for the tasks run now."""
    run = Path(run).resolve()
    (run / "_logs").mkdir(parents=True, exist_ok=True)
    (run / "_logs").chmod(0o700)          # during a Codex run in a container the folder holds a copy of the key
    open_tasks, done = [], 0
    for name, level, task_dir in todo:
        ws, log = run / name, run / "_logs" / name
        if (log / ".exit_code").is_file():
            done += 1
            continue
        if log.exists():             # started, never finished
            n = 1
            while (run / "_interrupted" / f"{name}.{n}").exists():
                n += 1
            keep = run / "_interrupted" / f"{name}.{n}"
            keep.mkdir(parents=True)
            if ws.exists():
                shutil.move(str(ws), str(keep / "workspace"))
            shutil.move(str(log), str(keep / "logs"))
            say(f"{name}: interrupted earlier, moved to {keep.relative_to(run)}; starting again from a fresh workspace")
        if not ws.exists():
            export_one(task_dir, ws)
        open_tasks.append((name, level))
    say(f"{len(open_tasks)} tasks to run, {done} already finished")
    if not open_tasks:
        return {}
    if cfg["docker"]:
        docker_ready(cfg)
    results = {}

    def work(item):
        name, level = item
        if STOPPING.is_set():
            return name, None
        try:
            rc, seconds = run_task(cfg, run, name, level)
        except Interrupted:
            return name, None
        note = "  (stopped at the time limit)" if rc == TIMED_OUT else ""
        say(f"{time.strftime('%F %T')} {name} exit={rc} {seconds // 60}m{seconds % 60:02d}s{note}")
        return name, {"exit_code": rc, "seconds": seconds}

    with concurrent.futures.ThreadPoolExecutor(max(1, cfg["jobs"])) as pool:
        futures = [pool.submit(work, item) for item in open_tasks]
        try:
            for f in futures:
                name, row = f.result()
                if row:
                    results[name] = row
        except KeyboardInterrupt:
            STOPPING.set()
            for stop in list(RUNNING.values()):
                stop()
            say("stopped. The tasks that were running start again from a fresh workspace when the same command is run again.")
            raise
    return results
