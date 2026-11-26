#!/usr/bin/env python3
"""Check a built game the way a player would meet it.

    python3 tools/ci_check.py [screenshot.png]

1. The packaged executable runs its engine selftest (no window).
2. It opens its window on a fresh save and is still running eight seconds
   later, with no crash log; a screenshot is saved for a person to look at.
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
import build  # noqa: E402

exe = build.executable()
shot = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "release", "screenshot.png")
print("checking", exe)

run = subprocess.run([exe, "--selftest"], capture_output=True, text=True, timeout=120)
print(run.stdout.strip() or "(no output)", run.stderr.strip()[-500:])
if run.returncode != 0:
    sys.exit("selftest failed with exit code %d" % run.returncode)

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "check.db")
env = dict(os.environ, TIMESHARE_DIAG="1")
game = subprocess.Popen([exe, "--db", db], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, env=env)
time.sleep(8)
alive = game.poll() is None
try:
    from PIL import ImageGrab
    os.makedirs(os.path.dirname(os.path.abspath(shot)), exist_ok=True)
    ImageGrab.grab().save(shot)
    print("screenshot", shot)
except Exception as exc:                 # a missing screenshot is not a failed build
    print("no screenshot:", exc)
game.terminate()
try:
    said, _ = game.communicate(timeout=10)
except subprocess.TimeoutExpired:
    game.kill()
    said, _ = game.communicate()
print("the game said:", (said or "").strip()[-600:] or "(nothing)")
crash = os.path.join(tmp, "timeshare-crash.log")
if os.path.exists(crash):
    print(open(crash).read())
    sys.exit("the game wrote a crash log")
if not alive:
    sys.exit("the game window closed by itself (exit code %s)" % game.returncode)
if not os.path.exists(db):
    sys.exit("the game did not create its save")
print("OK: the window ran, the save was made, nothing crashed")
