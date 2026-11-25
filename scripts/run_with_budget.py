"""Run a command and stop it when the account's usage reaches the pause line.

    python scripts/run_with_budget.py [--session 40] [--week 45] -- <command...>

Every few minutes it reads `claude -p /usage` (the account this environment
points at). When the 5-hour session or the weekly figure reaches its line,
the command is stopped; pipeline work done so far is in the model cache, so
the same command later resumes from there. Exit code 3 = stopped for budget.
"""
import os
import re
import subprocess
import sys
import time


def usage() -> tuple[int, int] | None:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    try:
        out = subprocess.run(["claude", "-p", "/usage"], capture_output=True, text=True,
                             encoding="utf-8", timeout=120, env=env, shell=os.name == "nt").stdout
    except subprocess.TimeoutExpired:
        return None
    s = re.search(r"Current session: (\d+)%", out)
    w = re.search(r"Current week \(all models\): (\d+)%", out)
    return (int(s.group(1)), int(w.group(1))) if s and w else None


def main(argv: list[str]) -> int:
    lines = {"--session": 40, "--week": 45, "--every": 180}
    while argv and argv[0] != "--":
        lines[argv[0]] = int(argv[1])
        argv = argv[2:]
    cmd = argv[1:]
    proc = subprocess.Popen(cmd)
    while proc.poll() is None:
        u = usage()
        stamp = time.strftime("%H:%M")
        print(f"[budget {stamp}] session {u[0] if u else '?'}%, week {u[1] if u else '?'}%",
              file=sys.stderr, flush=True)
        if u and (u[0] >= lines["--session"] or u[1] >= lines["--week"]):
            print(f"[budget {stamp}] pause line reached; stopping", file=sys.stderr, flush=True)
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)] if os.name == "nt"
                           else ["kill", str(proc.pid)], capture_output=True)
            proc.wait()
            return 3
        for _ in range(lines["--every"]):
            if proc.poll() is not None:
                break
            time.sleep(1)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
