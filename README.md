# timeshare

A fake 1979 timesharing operator, played over a socket. You buy dial-in
lines, cores and tape, hire staff, sell batch slots, and try to keep the
books positive while the machine ages and things break.

## run

```
python3 main.py --port 2023 --db /tmp/demo.db
python3 play.py                      # second terminal
python3 main.py --port 2023 --web 2024   # browser board on :2024
```

Stdlib only. No install step.

## checks

```
python3 tools/smoke.py 127.0.0.1 2023
```

All the fiction and every number live in `content.py`. `main.py` is the
engine; `tools/` are the checks that keep it honest.
