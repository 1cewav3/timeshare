# timeshare

A fake 1979 timesharing operator, played over a socket. You buy dial-in
lines, cores and tape, hire staff, sell batch slots, and try to keep the
books positive while the machine ages and things break.

## run

```
python3 main.py --port 2023 --db /tmp/demo.db
python3 play.py                      # second terminal
python3 main.py --port 2023 --web 2024   # browser board on :2024
python3 panel.py 127.0.0.1 2023 2024     # the machine, drawn as a panel
python3 machine.py 127.0.0.1 2023 2024   # the machine, as its own window
```

The panel needs the board port: it reads the numbers from `/state` and keeps
one socket session open so the game counts you as on the line.

Stdlib only. No install step.

## world clock

The world runs on the clock whether or not anyone is dialled in, and catches
up when you come back. If you would rather it only moved while you are
playing:

```
MODE SESSION
```

`MODE WALL` puts it back on the clock. The choice is saved with the world.

## checks

```
python3 tools/smoke.py 127.0.0.1 2023
```

All the fiction and every number live in `content.py`. `main.py` is the
engine; `tools/` are the checks that keep it honest.

## license

MIT. See `LICENSE`.
