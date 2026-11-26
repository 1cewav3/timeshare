# timeshare

A fake 1979 timesharing operator, played over a socket. You buy dial-in
lines, cores and tape, hire staff, sell batch slots, and try to keep the
books positive while the machine ages and things break.

## run

```
python3 machine.py                        # starts the world and opens the window
python3 machine.py --db /tmp/demo.db --tick 3
python3 play.py                           # plain terminal client, same world
```

`machine.py` alone is enough: if no world answers on the command port it
starts one, and closing the window stops it again. Add `--port`, `--web`,
`--db` or `--tick` to point it at a different world.

Stdlib only. No install step.

## world clock

The world runs on the clock whether or not anyone is dialled in, and catches
up when you come back: the sheet opens on a report of what happened while you
were gone (`AWAY` prints it again). If you would rather it only moved while you are
playing:

```
MODE SESSION
```

`MODE WALL` puts it back on the clock. The choice is saved with the world.

## checks

```
python3 main.py --port 2099 --db /tmp/test.db --tick 1 &   # a throwaway world
python3 tools/smoke.py 127.0.0.1 2099 --force
for t in away lesson statement incident site plant business sell liquidate tycoon contract goal rep; do
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

## license

MIT. See `LICENSE`.
