# timeshare

A fake 1979 timesharing bureau, in one window. You buy dial-in lines and
processing modules, hire staff, take contracts, answer what the day throws at
you, and keep the books positive while the machine ages and things break.

## play

Download the file for your system from the
[Releases page](https://github.com/1cewav3/timeshare/releases) (or the itch.io
page), unpack it, and start TIMESHARE. Nothing to install; the READ ME inside
says how on each system.

## run from source

```
python3 machine.py                        # play
python3 machine.py --db /tmp/demo.db      # play a different save
```

One window, single player. The world runs inside the game's own process: it
opens no network ports and needs no server. Stdlib only, no install step.

The save lives in the usual place for app data:

| | |
|---|---|
| Windows | `%APPDATA%\Timeshare\timeshare.db` |
| Linux | `~/.local/share/Timeshare/timeshare.db` |

A save from an older version in `~/timeshare/` is used where it is. Only one
window can have a save open at a time.

## world clock

The world keeps time while the game is closed. When you come back the window
catches up on what you missed, then opens on a report of what happened while
you were gone (`AWAY` prints it again). If you would rather it only moved while
the game is open:

```
MODE SESSION
```

`MODE WALL` puts it back on the clock. The choice is saved with the world.

## checks

The socket checks need the developer's server, which the game itself never
starts. It listens on this machine only:

```
python3 main.py --port 2099 --db /tmp/test.db --tick 1 &   # a throwaway world
python3 tools/smoke.py 127.0.0.1 2099 --force
for t in session away lesson statement incident site plant business sell liquidate tycoon contract goal rep; do
    python3 tools/${t}_check.py
done
```

Never point the smoke test at a world someone is playing: it spends money.

`tools/balance_check.py` is slower (about two minutes): it runs the balance
sweep and checks its shape. Doing everything beats any one system, every
investment pays over three weeks, and doing nothing ends in liquidation.
`python3 main.py --sim 10080 --seeds 3` prints the sweep itself.

All the fiction and every number live in `content.py`. `main.py` is the
engine; `tools/` are the checks that keep it honest.

## releasing

See [RELEASING.md](RELEASING.md): a version tag builds Windows and Linux
on GitHub and publishes them.

## license

MIT. See `LICENSE`.
