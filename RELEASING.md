# Releasing

A release is a version tag. GitHub builds the game on Windows, macOS and Linux,
checks each build, and publishes them on the repository's Releases page. If an
itch.io key is set up, the same builds go to the itch page too.

## Cutting a release

1. Set the version in `main.py` (`VERSION = "0.1.0"`).
2. Add a section for it at the top of `CHANGELOG.md` (`## 0.1.0`). Its text
   becomes the release notes.
3. Commit, then tag and push the tag:

   ```
   git tag v0.1.0
   git push origin main v0.1.0
   ```

4. Watch the **Actions** tab. Each platform builds, runs its selftest, opens
   its window on a fresh save, and takes a screenshot. The screenshots are in
   the run's artifacts: look at them before telling anyone.
5. The **Releases** page then has `v0.1.0` with four downloads:

   | File | For |
   |---|---|
   | `timeshare-0.1.0-windows-x86_64.zip` | Windows 10 and 11 |
   | `timeshare-0.1.0-macos-arm64.zip` | Macs with Apple Silicon (M1 and later) |
   | `timeshare-0.1.0-macos-x86_64.zip` | Intel Macs (built while GitHub still offers Intel runners) |
   | `timeshare-0.1.0-linux-x86_64.tar.gz` | 64-bit Linux |

To build and check without publishing, run the workflow by hand: Actions,
release, Run workflow. Nothing is published unless the run is for a tag.

## itch.io

### By hand

On the game's itch page: Edit game, Uploads, Upload files. Upload the four
archives from the release, and tick the platform for each (Windows, macOS,
Linux; both Mac files are macOS). Set the kind of project to "Downloadable".

### Automatically

1. On itch.io: Settings, API keys, Generate new API key.
2. On GitHub: Settings, Secrets and variables, Actions.
   - New repository **secret** `BUTLER_API_KEY`: the key.
   - New repository **variable** `ITCH_GAME`: `your-itch-name/timeshare`.
3. Every release tag then also pushes to itch, one channel per platform
   (`windows`, `mac`, `mac-intel`, `linux`), so the itch app can install and
   update the game.

## Building on your own machine

```
python3 -m venv .venv && .venv/bin/pip install pyinstaller pillow
.venv/bin/python tools/build.py      # this OS only, into release/
.venv/bin/python tools/ci_check.py   # the same check the workflow runs
```

`tools/make_icon.py` redraws `assets/icon.png`.

## Before a release, by hand

- `python3 tools/balance_check.py` (slow): the balance still has its shape.
- Play a fresh bureau for ten minutes on the build itself, not from source.
- Unsigned builds: Windows shows "Windows protected your PC" (More info, Run
  anyway); macOS asks once (right-click, Open). The READ ME in each archive
  says so. Signing needs paid certificates and can come later.
