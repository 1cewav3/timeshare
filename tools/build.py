#!/usr/bin/env python3
"""Build the game for the operating system this runs on, and pack it for release.

    pip install pyinstaller pillow
    python3 tools/build.py

Writes release/timeshare-<version>-<os>-<arch>.zip (Windows, macOS) or .tar.gz
(Linux): the game with Python, Tk and everything else inside it, and a READ ME
for the player. Each OS has to be built on that OS; .github/workflows/release.yml
does all of them on GitHub's machines.
"""
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import main  # noqa: E402  (for the version only)

NAME = "TIMESHARE"
WORK = os.path.join(HERE, "build")
DIST = os.path.join(WORK, "dist")
OUT = os.path.join(HERE, "release")


def target():
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return system, arch


def executable():
    """Where the built game ends up, for running it."""
    system, _ = target()
    if system == "windows":
        return os.path.join(DIST, NAME + ".exe")
    if system == "macos":
        return os.path.join(DIST, NAME + ".app", "Contents", "MacOS", NAME)
    return os.path.join(DIST, NAME)


def build():
    system, _ = target()
    icon = os.path.join(HERE, "assets", "icon.png")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
            "--name", NAME, "--icon", icon,
            "--add-data", icon + os.pathsep + "assets",
            "--distpath", DIST, "--workpath", os.path.join(WORK, "work"),
            "--specpath", WORK]
    if system == "macos":
        # an .app is a folder; one-file .app bundles are deprecated
        args += ["--osx-bundle-identifier", "org.timeshare.game"]
    else:
        args += ["--onefile"]
    args.append(os.path.join(HERE, "machine.py"))
    subprocess.run(args, check=True, cwd=HERE)


def pack():
    system, arch = target()
    version = main.VERSION
    os.makedirs(OUT, exist_ok=True)
    folder = "%s-%s" % (NAME, version)
    readme = open(os.path.join(HERE, "packaging", "READ ME.txt")).read().replace("{version}", version)
    base = os.path.join(OUT, "timeshare-%s-%s-%s" % (version, system, arch))
    stage = os.path.join(WORK, "stage", folder)
    shutil.rmtree(os.path.dirname(stage), ignore_errors=True)
    os.makedirs(stage)
    with open(os.path.join(stage, "READ ME.txt"), "w", newline="\r\n" if system == "windows" else "\n") as fh:
        fh.write(readme)
    if system == "macos":
        shutil.copytree(os.path.join(DIST, NAME + ".app"), os.path.join(stage, NAME + ".app"),
                        symlinks=True)
        path = base + ".zip"
        if os.path.exists(path):
            os.remove(path)
        # ditto keeps the bundle's symlinks and permissions; zipfile would not
        subprocess.run(["ditto", "-c", "-k", "--keepParent", stage, path], check=True)
    elif system == "windows":
        shutil.copy2(executable(), stage)
        path = base + ".zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(os.listdir(stage)):
                z.write(os.path.join(stage, name), folder + "/" + name)
    else:
        shutil.copy2(executable(), stage)
        os.chmod(os.path.join(stage, NAME), 0o755)
        path = base + ".tar.gz"
        with tarfile.open(path, "w:gz") as t:
            t.add(stage, arcname=folder)
    return path


if __name__ == "__main__":
    build()
    print("built", executable())
    print("packed", pack())
