#!/usr/bin/env python3
"""The browser board is a second front end, so it gets its own test.

Starts a throwaway server with the web listener, then exercises the page, the
state snapshot and the command endpoint - including a command that takes the
lock, which is what deadlocked the first version.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME_PORT, WEB_PORT = 2131, 2132


def free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def get(url, data=None, timeout=8):
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:      # 404s are answers too
        return e.code, e.read()


def main():
    checks = []
    if not free(GAME_PORT) or not free(WEB_PORT):
        print("ports busy; stop the servers first")
        return 2
    db = "/tmp/webcheck.db"
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(db + suffix)
        except FileNotFoundError:
            pass
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "main.py"),
                             "--port", str(GAME_PORT), "--web", str(WEB_PORT),
                             "--db", db, "--tick", "1"],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                get("http://127.0.0.1:%d/state" % WEB_PORT, timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        else:
            print("server never came up")
            return 1

        status, body = get("http://127.0.0.1:%d/" % WEB_PORT)
        checks.append(("the page is served", status == 200 and b"NORTHGATE" in body))

        status, body = get("http://127.0.0.1:%d/style.css" % WEB_PORT)
        checks.append(("the stylesheet is served", status == 200 and b"--amber" in body))

        status, body = get("http://127.0.0.1:%d/state" % WEB_PORT)
        snap = json.loads(body)
        checks.append(("state is JSON with the numbers the board draws",
                       all(k in snap for k in ("cash", "capacity", "served", "goal", "history"))))
        checks.append(("the snapshot carries the goal", snap["goal"]["price"] > 0))

        payload = json.dumps({"line": "status"}).encode()
        status, body = get("http://127.0.0.1:%d/cmd" % WEB_PORT, payload)
        out = json.loads(body)["out"]
        checks.append(("commands run over HTTP", "SYSTEM REPORT" in out))

        payload = json.dumps({"line": "hire clerk"}).encode()
        status, body = get("http://127.0.0.1:%d/cmd" % WEB_PORT, payload, timeout=12)
        out = json.loads(body)["out"]
        checks.append(("a command that takes the lock does not deadlock",
                       "OK:" in out or "ERR:" in out))

        payload = json.dumps({"line": "sites"}).encode()
        status, body = get("http://127.0.0.1:%d/cmd" % WEB_PORT, payload)
        checks.append(("the new screens work over HTTP too", "SITES" in json.loads(body)["out"]))

        status, body = get("http://127.0.0.1:%d/../../etc/passwd" % WEB_PORT)
        checks.append(("path traversal is refused", status == 404 and b"root:" not in body))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        err = proc.stderr.read().decode() if proc.stderr else ""
        if "Traceback" in err:
            checks.append(("no server-side traceback", False))
            print(err[-800:])

    bad = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    print("\n%d/%d passed" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
